import json
from typing import List, Optional, Dict, Tuple, AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio
from sqlalchemy.orm import sessionmaker
from ..db.session import engine  # Add this if not already imported
from sqlalchemy import select, update, and_, cast, String
import uuid
from datetime import datetime
from asyncio import TimeoutError
from async_timeout import timeout

from ..models.database import Document, DocumentChunk, Conversation, Message, Question, ConversationType, User
from ..core.events import event_bus, Event
from ..core.config import settings
from ..core.logging import setup_logger
from .ai import AIService
from ..prompts.manager import PromptManager
from src.services.cost import check_user_cost_limit

logger = setup_logger(__name__)

class ConversationService:
    _instance = None
    _initialized = False

    def __new__(cls, db: AsyncSession = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db: AsyncSession = None):
        if not self._initialized and db is not None:
            self.AsyncSessionLocal = sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=True
            )
    
            self.ai_service = AIService()  # Remove db dependency
            self.prompt_manager = PromptManager()
                
            # Task management
            self.task_queue = asyncio.Queue()
            self.active_tasks = set()
            # self.semaphore = asyncio.Semaphore(10)
            self.shutdown_event = asyncio.Event()
                
            # Start processor
            self.processor_task = asyncio.create_task(self._process_tasks())

            # Register event handlers with queue wrapper
            event_bus.on("conversation.main.create.requested", self._queue_task(self.handle_create_main_conversation))
            event_bus.on("conversation.chunk.create.requested", self._queue_task(self.handle_create_chunk_conversation))
            event_bus.on("conversation.message.send.requested", self._queue_task(self.handle_send_message))
            event_bus.on("conversation.questions.generate.requested", self._queue_task(self.handle_generate_questions))
            event_bus.on("conversation.questions.list.requested", self._queue_task(self.handle_list_questions))
            event_bus.on("conversation.merge.requested", self._queue_task(self.handle_merge_conversations))
            event_bus.on("conversation.chunk.get.requested", self._queue_task(self.handle_get_conversations_by_sequence))
            event_bus.on("conversation.messages.requested", self._queue_task(self.handle_list_messages))
            event_bus.on("conversation.list.requested", self._queue_task(self.handle_list_conversations))
            event_bus.on("conversation.questions.regenerate.requested", self._queue_task(self.handle_regenerate_questions))
                
            self.__class__._initialized = True

    def _queue_task(self, handler):
        async def wrapper(event):
            if not self.shutdown_event.is_set():
                await self.task_queue.put((handler, event))
        return wrapper

    async def _process_tasks(self):
      while not self.shutdown_event.is_set():
          try:
              handler, event = await self.task_queue.get()
              task = asyncio.create_task(self._run_task(handler, event))
              self.active_tasks.add(task)
              task.add_done_callback(self._cleanup_task)
          except asyncio.CancelledError:
              break
          except Exception as e:
              logger.error(f"Task processor error: {e}")
      
      # Cleanup when processor stops
      await self._cleanup_all_tasks()

    async def _run_task(self, handler, event):
      try:
          async with timeout(25):  # Changed from asyncio.timeout(25)
              # async with self.semaphore:
                try:
                    async with self.AsyncSessionLocal() as db:
                        try:
                            logger.info(f"Running task: {event.type}")
                            await handler(event, db)
                        except Exception as e:
                            logger.error(f"Handler error: {e}")
                        finally:
                            await db.close()
                except Exception as e:
                    logger.error(f"DB session error: {e}")
                      # self.semaphore.release()
      except TimeoutError:  # Changed from asyncio.TimeoutError
          logger.error(f"Task timed out: {event.type}")
          # self.semaphore.release()
      except Exception as e:
          logger.error(f"Task execution error: {e}")
          # self.semaphore.release()

    async def handle_create_main_conversation(self, event: Event, db: AsyncSession):
        try:
            document_id = event.document_id
            is_demo = document_id in settings.EXAMPLE_DOCUMENT_IDS
            
            # First check if a main conversation already exists
            query = select(Conversation).where(
                and_(
                    Conversation.document_id == document_id,
                    Conversation.type == "main"
                )
            )
            
            # For demo docs, only find main conversations from this connection
            if is_demo:
                logger.info(f"Looking for demo main conversation with connection_id: {event.connection_id}")
                query = query.where(
                    and_(
                        Conversation.is_demo == True,
                        Conversation.meta_data['connection_id'].astext == event.connection_id
                    )
                )
            
            result = await db.execute(query)
            existing_conversation = result.scalar_one_or_none()
            
            if existing_conversation:
                # Return the existing conversation ID
                logger.info("Returning existing main conversation")
                await event_bus.emit(Event(
                    type="conversation.main.create.completed",
                    document_id=document_id,
                    connection_id=event.connection_id,
                    request_id=event.request_id,
                    data={"conversation_id": str(existing_conversation.id)}
                ))
                return
            
            # If no main conversation exists, create one
            # Get first chunk
            first_chunk = await self._get_first_chunk(document_id, db)
            
            # Create conversation with connection_id in meta_data for demo conversations
            meta_data = {
                "seen_chunks": []
            }
            # Initialize metadata for main conversation
            if is_demo:
                meta_data["connection_id"] = event.connection_id
            chunk_id = "0"    
            conversation = await self._create_conversation(
                document_id=document_id,
                conversation_type="main",
                chunk_id=chunk_id,
                db=db,
                meta_data=meta_data,
                is_demo=is_demo
            )

            # Create initial system message
            system_prompt = self.prompt_manager.create_main_system_prompt(first_chunk["content"])
            message_obj = await self._create_message(
                conversation["id"], 
                system_prompt,
                chunk_id,
                "system",
                db=db
            )
            
            await db.commit()
            logger.info(f"Created new main conversation for connection {event.connection_id}")

            await event_bus.emit(Event(
                type="conversation.main.create.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"conversation_id": str(conversation["id"])}
            ))
        except Exception as e:
            logger.error(f"Error creating main conversation: {e}")
            await event_bus.emit(Event(
                type="conversation.main.create.error",
                document_id=document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"error": str(e)}
            ))

    async def handle_create_chunk_conversation(self, event: Event, db: AsyncSession):
        try:
            data = event.data
            document_id = event.document_id
            chunk_id = data["chunk_id"]
            highlight_text = data.get("highlight_text", "")
            is_demo = document_id in settings.EXAMPLE_DOCUMENT_IDS
            
            # Get chunk
            chunk = await self._get_chunk(document_id=document_id, chunk_id=chunk_id, db=db)  # Pass db
            
            # Create conversation meta_data
            meta_data = {
                "highlight_text": highlight_text,
                "highlight_range": data["highlight_range"]
            }
            if is_demo:
                meta_data["connection_id"] = event.connection_id
                
            # Create conversation
            logger.info("Creating new chunk conversation (highlight) and rewriting metadata")
            conversation = await self._create_conversation(
                document_id=document_id,
                conversation_type="highlight",
                chunk_id=chunk_id,
                db=db,
                meta_data=meta_data,
                is_demo=is_demo
            )
            # Create initial system message
            system_prompt = self.prompt_manager.create_highlight_system_prompt(
                chunk_text=chunk["content"],
                highlight_text=highlight_text
            )
            await self._create_message(
                conversation["id"], 
                system_prompt, 
                chunk_id,
                "system",
                db=db  # Pass db
            )
            
            await db.commit()

            questions_request_id = f"{event.request_id}_questions"
            await self.handle_generate_questions(Event(
                type="conversation.questions.generate.requested",
                connection_id=event.connection_id,
                request_id=questions_request_id,
                document_id=document_id,
                data={
                    "conversation_id": str(conversation["id"]),
                    "user": event.data.get("user", None),
                    "document_id": document_id,
                    "chunk_id": event.data.get("chunk_id", None)
                }
            ), db)
            
            await event_bus.emit(Event(
                type="conversation.chunk.create.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"conversation_id": str(conversation["id"])}
            ))
        except Exception as e:
            logger.error(f"Error creating chunk conversation: {e}")
            await event_bus.emit(Event(
                type="conversation.chunk.create.error",
                document_id=document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"error": str(e)}
            ))

    async def handle_send_message(self, event: Event, db: AsyncSession):
        logger.info("Handle send message")
        document_id = event.document_id
        try:
            conversation_id = event.data["conversation_id"]
            content = event.data["content"]
            chunk_id = event.data["chunk_id"]
            user = event.data.get("user", None)
            use_full_context = event.data.get("use_full_context", False)

            # Check cost limit first if user exists
            logger.info("Checking user cost limit for user: %s", user.id if user else "None")
            if user:
                await check_user_cost_limit(db, user.id)
            
            # Get conversation
            conversation = await self._get_conversation(conversation_id, db)
            chunk = await self._get_chunk(document_id=conversation["document_id"], chunk_id=conversation["chunk_id"], db=db) if conversation["chunk_id"] else None

            # 1. Store raw user message in DB
            message = await self._create_message(
                conversation["id"], 
                content, 
                chunk_id if chunk_id else conversation["chunk_id"],
                "user",
                db=db
            )
            await db.commit()
            
            # 2. Process the message and build context
            messages = []
            if use_full_context:
                # Get full document content
                full_document_text = await self._get_all_chunks(document_id, db)

                # Get all conversation messages
                all_messages = await self.get_conversation_messages(conversation["id"], db)
                
                # Add system prompt based on conversation type
                if conversation["type"] == "highlight":
                    highlight_text = await self._get_highlight_text(conversation["id"], db)
                    system_prompt = self.prompt_manager.create_full_context_highlight_system_prompt(
                        full_document_text=full_document_text,
                        highlight_text=highlight_text
                    )
                else:
                    system_prompt = self.prompt_manager.create_full_context_system_prompt(
                        full_document_text=full_document_text
                    )
                
                messages.append({"role": "system", "content": system_prompt})
                
                # Add conversation history
                messages.extend(all_messages)
                
                # Add current user message
                messages.append({
                    "role": "user",
                    "content": content
                })
            else:
                # Regular context handling
                if conversation["type"] == "highlight":
                    highlight_text = await self._get_highlight_text(conversation["id"], db)
                    processed_content = self.prompt_manager.create_highlight_user_prompt(
                        user_message=content
                    )
                    messages = await self.get_conversation_messages(conversation["id"], db)
                else:
                    processed_content = self.prompt_manager.create_main_user_prompt(
                        user_message=content
                    )
                    messages = await self._get_messages_with_chunk_switches(conversation, db)

                if messages:
                    messages[-1]["content"] = processed_content

            # 3. Send messages to AI service
            response, cost = await self.ai_service.chat(
                document_id,
                conversation_id,
                messages=messages,
                connection_id=event.connection_id,
                request_id=event.request_id,
                chat_model="gemini-2.0-flash-exp" if use_full_context else None,
            )
                        
            # Add AI response in a new transaction
            ai_message = await self._create_message(
                conversation["id"], 
                response, 
                chunk_id if chunk_id else conversation["chunk_id"],
                "assistant",
                db=db  # Pass db
            )
            if user and cost:
                stmt = select(User).where(User.id == user.id)
                result = await db.execute(stmt)
                db_user = result.scalar_one()
                db_user.user_cost += cost
                logger.info(f"Updated user {user.id} cost to ${db_user.user_cost:.2f}")

            # If this message was from a suggested question, mark it as answered
            question_id = event.data.get("question_id")
            if question_id:
                question = await db.execute(
                    select(Question)
                    .where(
                        Question.id == question_id,
                        Question.conversation_id == conversation_id
                    )
                )
                question = question.scalar_one_or_none()
                if question:
                    question.answered = True
        
            await db.commit()
            
            await event_bus.emit(Event(
                type="conversation.message.send.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"message": response, "conversation_id": str(conversation["id"]), "cost": cost}
            ))
                
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            await event_bus.emit(Event(
                type="conversation.message.send.error",
                document_id=document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"error": str(e)}
            ))
            try:
                await db.rollback()
            except:
                pass

    async def handle_generate_questions(self, event: Event, db: AsyncSession, emit_event: bool = True):
        try:
            conversation_id = event.data["conversation_id"]
            document_id = event.document_id
            user = event.data.get("user", None)
            chunk_id = event.data.get("chunk_id", None)
            # Check cost limit first if user exists
            if user:
                await check_user_cost_limit(db, user.id)
            
            # Get conversation and chunk
            conversation = await self._get_conversation(conversation_id, db)  # Pass db
            chunk = await self._get_chunk(document_id=document_id, chunk_id=chunk_id, db=db) 
            
            # Get previous questions
            previous_questions = await self._get_previous_questions(conversation_id, chunk_id, db)  # Pass db
            # logger.info(f"Previous questions count: {len(previous_questions)}")
        
            # Generate questions based on conversation type
            if conversation["type"] == "highlight":
                highlight_text = await self._get_highlight_text(conversation_id, db)  # Pass db
                system_prompt, user_prompt = self.prompt_manager.create_highlight_question_prompts(
                    chunk_text=chunk["content"],
                    highlight_text=highlight_text,
                    count=3,
                    previous_questions=previous_questions
                )
            else:
                system_prompt, user_prompt = self.prompt_manager.create_main_question_prompts(
                    chunk_text=chunk["content"],
                    count=3,
                    previous_questions=previous_questions
                )
            
            questions, cost = await self.ai_service.generate_questions(
                document_id=document_id,
                request_id=event.request_id,
                conversation_id=conversation_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt
            )

            if user and cost:
                stmt = select(User).where(User.id == user.id)
                result = await db.execute(stmt)
                db_user = result.scalar_one()
                db_user.user_cost += cost
                logger.info(f"Updated user {user.id} cost to ${db_user.user_cost:.2f}")
            
            questions_to_add = []
            for question in questions[:3]:
                if question.strip():
                    new_question = Question(
                        conversation_id=conversation_id,
                        content=question.strip(),
                        meta_data={"chunk_id": chunk_id},
                        answered=False
                    )
                    questions_to_add.append(new_question)
            db.add_all(questions_to_add)
            await db.commit()
            logger.info(f"Successfully added {len(questions_to_add)} questions to database")

            if emit_event:
                await event_bus.emit(Event(
                    type="conversation.questions.generate.completed",
                    document_id=document_id,
                    connection_id=event.connection_id,
                    request_id=event.request_id,
                    data={
                        "conversation_id": conversation_id,
                        "questions": [q.strip() for q in questions[:3]],
                        "cost": cost
                    }
                ))
                
            return [q.strip() for q in questions[:3]]  # Return questions for reuse
                
        except Exception as e:
            logger.error(f"Error generating questions: {e}")
            await event_bus.emit(Event(
                type="conversation.questions.generate.error",
                document_id=document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"error": str(e)}
            ))

    async def handle_merge_conversations(self, event: Event, db: AsyncSession):
        try:
            data = event.data
            document_id = event.document_id
            main_conversation_id = data["main_conversation_id"]
            highlight_conversation_id = data["highlight_conversation_id"]
            
            # Get conversations
            highlight_conversation = await self._get_conversation(highlight_conversation_id, db)
            chunk = await self._get_chunk(document_id=document_id, chunk_id=highlight_conversation["chunk_id"], db=db)
            highlight_text = await self._get_highlight_text(highlight_conversation_id, db)
            
            # Format conversation history for summary
            conversation_history = await self._format_conversation_history(highlight_conversation_id, db)
            
            # Generate summary using AI service
            system_prompt, user_prompt = self.prompt_manager.create_summary_prompts(
                chunk_text=chunk["content"],
                highlight_text=highlight_text,
                conversation_history=conversation_history
            )
            
            summary, cost = await self.ai_service.generate_summary(
                document_id=document_id,
                request_id=event.request_id,
                conversation_id=highlight_conversation_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt
            )
            
            # Get metadata from first message for chunk info
            first_message = await self._get_first_message(highlight_conversation_id, db)
            chunk_id = first_message.get("meta_data", {}).get("chunk_id") if first_message.get("meta_data") else None
            
            # Create summary message in main conversation
            await self._create_message(
                conversation_id=main_conversation_id,
                content=f"Summary of highlight discussion:\n{summary}",
                chunk_id=chunk_id,
                role="user",
                meta_data={"merged_from": highlight_conversation_id},
                db=db
            )
            
            # Add acknowledgment message
            await self._create_message(
                conversation_id=main_conversation_id,
                content="Acknowledged conversation merge",
                chunk_id=chunk_id,
                role="assistant",
                db=db
            )

            await db.commit()
            
            await event_bus.emit(Event(
                type="conversation.merge.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={
                    "main_conversation_id": main_conversation_id,
                    "highlight_conversation_id": highlight_conversation_id,
                    "summary": summary,
                    "cost": cost
                }
            ))
            
        except Exception as e:
            logger.error(f"Error merging conversations: {e}")
            await event_bus.emit(Event(
                type="conversation.merge.error",
                document_id=document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"error": str(e)}
            ))

    async def handle_list_questions(self, event: Event, db: AsyncSession):
      try:
          conversation_id = event.data["conversation_id"]
          document_id = event.document_id
          chunk_id = event.data.get("chunk_id", None)
          # Retrieve the conversation to access its metadata
          query = select(Conversation).where(Conversation.id == conversation_id)
          result = await db.execute(query)
          conversation = result.scalar_one_or_none()
          
          if conversation is None:
            raise ValueError(f"Conversation with ID {conversation_id} not found")

          seen_chunks = conversation.meta_data.get('seen_chunks', []).copy()
        
        # Generate new questions for this chunk
          if chunk_id not in seen_chunks:
            logger.info(f"Chunk {chunk_id} not in seen chunks. Generating questions.")
            updated_seen_chunks = seen_chunks + [str(chunk_id)]
            conversation.meta_data = {
                **conversation.meta_data,
                'seen_chunks': updated_seen_chunks
            }
            db.add(conversation)
            await db.commit()
            await db.refresh(conversation)
            
            # If chunk hasn't been seen, generate new questions
            await self.handle_generate_questions(event, db)

          questions = await self.get_conversation_questions_unanswered(conversation_id, db, chunk_id)
          await event_bus.emit(Event(
              type="conversation.questions.list.completed",
              document_id=document_id,
              connection_id=event.connection_id,
              request_id=event.request_id,
              data={
                  "conversation_id": conversation_id,
                  "chunk_id": chunk_id,
                  "questions": questions  # Send the full question objects
              }
          ))
          logger.info(f"Emitted questions list completed event with {len(questions)} questions")
      except Exception as e:
          logger.error(f"Error listing questions: {e}")
          await event_bus.emit(Event(
              type="conversation.questions.list.error",
              document_id=document_id,
              connection_id=event.connection_id,
              request_id=event.request_id,
              data={"error": str(e)}
          ))

    async def handle_regenerate_questions(self, event: Event, db: AsyncSession):
      try:
          conversation_id = event.data["conversation_id"]
          chunk_id = event.data.get("chunk_id", None)
          document_id = event.document_id
          # Mark existing questions as answered
          await db.execute(
              update(Question)
              .where(Question.conversation_id == conversation_id)
              .values(answered=True)
          )
          await db.commit()
          
          # Generate questions without event emission
          questions = await self.handle_generate_questions(
              Event(
                  type="conversation.questions.generate.requested",
                  document_id=document_id,
                  connection_id=event.connection_id,
                  data={
                      "conversation_id": conversation_id,
                      "document_id": document_id,
                      "user": event.data.get("user"),
                      "chunk_id": chunk_id
                  }
              ), 
              db,
              emit_event=False  # New parameter to control event emission
          )
          
          # Emit regeneration completed event
          await event_bus.emit(Event(
              type="conversation.questions.regenerate.completed",
              document_id=document_id,
              connection_id=event.connection_id,
              request_id=event.request_id,
              data={
                  "conversation_id": conversation_id,
                  "questions": questions  # Return the new questions
              }
          ))
          
      except Exception as e:
          logger.error(f"Error regenerating questions: {e}")
          await event_bus.emit(Event(
              type="conversation.questions.regenerate.error",
              document_id=document_id,
              connection_id=event.connection_id,
              request_id=event.request_id,
              data={"error": str(e)}
          ))

    async def handle_get_conversations_by_sequence(self, event: Event, db: AsyncSession):
        """Get all conversations for a document that have a specific chunk sequence number"""
        try:
            document_id = event.document_id
            sequence_number = event.data["sequence_number"]
            
            # Query conversations with the given document_id and chunk_id (sequence number)
            stmt = select(Conversation).where(
                and_(
                    Conversation.document_id == document_id,
                    Conversation.chunk_id == sequence_number
                )
            )
            
            result = await db.execute(stmt)
            conversations = result.scalars().all()

            conversations_data = {}
            for conv in conversations:
                conv_data = {
                    "document_id": str(conv.document_id),
                    "chunk_id": conv.chunk_id,
                    "created_at": conv.created_at.isoformat(),
                    "highlight_text": conv.meta_data.get("highlight_text", "") if conv.meta_data else ""
                }

                # Add highlight_range if available in meta_data
                if conv.meta_data and "highlight_range" in conv.meta_data:
                    conv_data["highlight_range"] = conv.meta_data["highlight_range"]

                conversations_data[str(conv.id)] = conv_data
            
            # Emit success event
            await event_bus.emit(Event(
                type="conversation.chunk.get.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,  # Preserve request_id
                data={"conversations": conversations_data}
            ))
            
        except Exception as e:
            logger.error(f"Error getting conversations by sequence: {str(e)}")
            await event_bus.emit(Event(
                type="conversation.chunk.get.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,  # Preserve request_id
                data={"error": str(e)}
            ))

    async def handle_list_conversations(self, event: Event, db: AsyncSession):
        """List all conversations for a document"""
        try:
            document_id = event.document_id
            is_demo = document_id in settings.EXAMPLE_DOCUMENT_IDS
            
            # Build query
            query = select(Conversation).where(
                Conversation.document_id == document_id
            )
            
            # For demo documents, only show conversations for this connection
            if is_demo:
                query = query.where(
                    and_(
                        Conversation.is_demo == True,
                        Conversation.meta_data['connection_id'].astext == event.connection_id
                    )
                )
            
            result = await db.execute(query)
            conversations = result.scalars().all()
            
            # Convert conversations to dict format with ID as key
            conversations_data = {
                str(conv.id): {
                    "document_id": str(conv.document_id),
                    "chunk_id": conv.chunk_id,
                    "created_at": conv.created_at.isoformat(),
                    "highlight_text": conv.meta_data.get("highlight_text") if conv.meta_data else ""
                }
                for conv in conversations
            }

            await event_bus.emit(Event(
                type="conversation.list.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"conversations": conversations_data}
            ))
            
        except Exception as e:
            logger.error(f"Error listing conversations: {str(e)}")
            await event_bus.emit(Event(
                type="conversation.list.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"error": str(e)}
            ))

    async def handle_list_messages(self, event: Event, db: AsyncSession):
        """Handle request to list messages for a conversation
        
        Args:
            event: Event containing conversation_id
            db: Database session
        """
        try:
            conversation_id = event.data.get("conversation_id")
            if not conversation_id:
                raise ValueError("Missing conversation_id")

            # Query messages for the conversation, ordered by timestamp
            stmt = select(Message).where(
                Message.conversation_id == conversation_id
            ).order_by(Message.created_at)
            
            result = await db.execute(stmt)
            messages = result.scalars().all()
            
            # Convert messages to dict format
            message_list = [{
                "id": str(msg.id),
                "role": msg.role,
                "content": msg.content,
                "created_at": msg.created_at.isoformat(),
                "conversation_id": str(msg.conversation_id)
            } for msg in messages]
            
            # Emit completion event with messages
            await event_bus.emit(Event(
                type="conversation.messages.completed",
                document_id=event.document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={
                    "conversation_id": conversation_id,
                    "messages": message_list
                }
            ))
            
        except Exception as e:
            logger.error(f"Error listing messages: {e}")
            # Emit error event
            await event_bus.emit(Event(
                type="conversation.messages.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={
                    "conversation_id": conversation_id,
                    "error": str(e)
                }
            ))

    async def _get_messages_with_chunk_switches(self, conversation: Dict, db: AsyncSession) -> List[Dict]:
      messages = await self.get_conversation_messages(conversation["id"], db)
      if conversation["type"] != "main":
          return messages

      processed_messages = []
      last_chunk_id = None
      system_message = None
      chunk_switches = []  # Track all switch messages for second pass

      # First find and add system message if it exists
      for msg in messages:
          if msg.get("role") == "system":
              system_message = msg
              break
      if system_message:
          processed_messages.append(system_message)

      # Process remaining messages
      for msg in messages:
          if msg.get("role") == "system":
              continue
              
          current_chunk_id = msg.get("chunk_id", "")
          if current_chunk_id and current_chunk_id != last_chunk_id:
              # Determine if this is a backwards jump
              is_backwards = last_chunk_id is not None and int(current_chunk_id) < int(last_chunk_id)
              
              if is_backwards:
                  # For backwards jumps, just switch to the single chunk
                  switch_msg = {
                      "role": "user",
                      "content": f"<switched to chunk ID {current_chunk_id}>"
                  }
              else:
                  # For forward progression, include range from last position
                  chunk_range = f"{last_chunk_id}-{current_chunk_id}" if last_chunk_id else str(current_chunk_id)
                  switch_msg = {
                      "role": "user",
                      "content": f"<switched to chunks {chunk_range}>"
                  }
              
              ack_msg = {
                  "role": "assistant",
                  "content": f"<acknowledged switch to chunk{'s' if not is_backwards and last_chunk_id else ''} {chunk_range if not is_backwards else current_chunk_id}>"
              }
              
              # Store switch info including range
              chunk_switches.append((
                  len(processed_messages),
                  current_chunk_id,
                  switch_msg,
                  last_chunk_id if not is_backwards else None
              ))
              
              processed_messages.append(switch_msg)
              processed_messages.append(ack_msg)
              
          processed_messages.append(msg)
          last_chunk_id = current_chunk_id

      # Second pass: Add chunk content only to the most recent switch for each chunk
      seen_chunks = set()
      for idx, current_chunk_id, switch_msg, last_chunk_id in reversed(chunk_switches):
          chunks_to_add = set()
          
          if last_chunk_id is None:
              # First chunk case - include everything up to this chunk
              chunks_to_add.update(str(i) for i in range(int(current_chunk_id) + 1))
          elif int(current_chunk_id) < int(last_chunk_id):
              # Backwards jump - just include current chunk
              chunks_to_add.add(current_chunk_id)
          else:
              # Forward progression - include range from last to current
              start = int(last_chunk_id)
              end = int(current_chunk_id)
              chunks_to_add.update(str(i) for i in range(start, end + 1))

          # Filter out already seen chunks
          chunks_to_add = chunks_to_add - seen_chunks
          
          if chunks_to_add:
              chunk_contents = []
              for chunk_id in sorted(chunks_to_add, key=int):
                  chunk = await self._get_chunk(document_id=conversation["document_id"], chunk_id=chunk_id, db=db)
                  if chunk:
                      chunk_contents.append(f"Chunk {chunk_id}: {chunk['content']}")
                      seen_chunks.add(chunk_id)
                      
              if chunk_contents:
                  processed_messages[idx]["content"] += f", chunkText: {' | '.join(chunk_contents)}"

      return processed_messages

    async def _get_current_chunk_id(self, conversation: Dict, db: AsyncSession) -> Optional[str]:
        if conversation["type"] != "main":
            return None
            
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation["id"])
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        last_message = result.scalar_one_or_none()
        
        if last_message and last_message.meta_data:
            return last_message.meta_data.get("chunk_id")
        return None

    async def _format_conversation_history(self, conversation_id: str, db: AsyncSession) -> str:
        messages = await self.get_conversation_messages(conversation_id=conversation_id, db=db)
        return "\n".join([
            f"{msg['role'].upper()}: {msg['content']}"
            for msg in messages
        ])

    async def _create_message(
        self,
        conversation_id: str,
        content: str,
        chunk_id: str,
        role: str = "user",
        meta_data: Optional[Dict] = None,
        db: AsyncSession = None
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            chunk_id=chunk_id,
            meta_data=meta_data
        )
        db.add(message)
        await db.flush()
        await db.refresh(message)
        return message

    async def get_conversation_messages(
        self, 
        conversation_id: str,
        db: AsyncSession,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict]:
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
            .offset(offset)
            .limit(limit)
        )
        messages = []
        for msg in result.scalars().all():
            messages.append(msg.to_dict())
        return messages

    async def get_conversation_questions(
        self,
        conversation_id: str,
        db: AsyncSession
    ) -> List[Dict]:
        result = await db.execute(
            select(Question)
            .where(Question.conversation_id == conversation_id)
            .order_by(Question.created_at)
        )
        return [q.to_dict() for q in result.scalars().all()]

    async def get_conversation_questions_unanswered(self, conversation_id: str, db: AsyncSession, chunk_id: str) -> List[Dict]:
      logger.info(f"Retrieving unanswered questions - Conversation ID: {conversation_id}, Chunk ID: {chunk_id}")
      result = await db.execute(
          select(Question)
          .where(
              and_(
                  Question.conversation_id == conversation_id,
                  Question.meta_data['chunk_id'].as_string() == str(chunk_id),
                  Question.answered == False
              )
          )
          .order_by(Question.created_at)
      )
      questions = result.scalars().all()
      return [q.to_dict() for q in questions]

    async def _get_conversation(self, conversation_id: str, db: AsyncSession) -> Dict:
        conversation = await db.get(Conversation, conversation_id)
        return conversation.to_dict() if conversation else None

    async def _get_previous_questions(self, conversation_id: str, chunk_id: str, db: AsyncSession) -> List[Dict]:
        result = await db.execute(
            select(Question)
            .where(Question.conversation_id == conversation_id)
            .where(cast(Question.meta_data['chunk_id'], String)== chunk_id)
            .order_by(Question.created_at)
        )
        return [q.to_dict() for q in result.scalars().all()]

    async def _get_highlight_text(self, conversation_id: str, db: AsyncSession) -> str:
        conversation = await self._get_conversation(conversation_id, db)
        return conversation.get("meta_data", {}).get("highlight_text", "")

    async def _get_first_message(self, conversation_id: str, db: AsyncSession) -> Dict:
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
            .limit(1)
        )
        return result.scalar_one().to_dict()

    async def _get_first_chunk(self, document_id: str, db: AsyncSession) -> Dict:
        result = await db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.sequence)
            .limit(1)
        )
        chunk = result.scalar_one()
        return chunk.to_dict() if chunk else None

    async def _get_chunk(self, document_id: str, chunk_id: int, db: AsyncSession) -> Optional[Dict]:
        """Get a chunk by its sequence number within a document"""
        try:
            # Convert chunk_id to integer if it's a string
            sequence = int(chunk_id) if isinstance(chunk_id, str) else chunk_id
            result = await db.execute(
                select(DocumentChunk)
                .where(
                    and_(
                        DocumentChunk.document_id == document_id,
                        DocumentChunk.sequence == sequence
                    )
                )
            )
            chunk = result.scalar_one_or_none()
            return chunk.to_dict() if chunk else None
        except ValueError:
            # Handle case where chunk_id can't be converted to int
            logger.error(f"Invalid chunk_id format: {chunk_id}")
            return None
      
    async def _get_all_chunks(self, document_id: str, db: AsyncSession) -> str:
      """Get all chunks concatenated into full document text"""
      chunks = await db.execute(
          select(DocumentChunk)
          .where(DocumentChunk.document_id == document_id)
          .order_by(DocumentChunk.sequence)
      )
      chunks = chunks.scalars().all()
      return "\n\n".join(chunk.content for chunk in chunks)

    async def _create_conversation(
        self,
        document_id: str,
        conversation_type: str,
        chunk_id: Optional[str] = None,
        db: AsyncSession = None,
        meta_data: Optional[Dict] = None,
        is_demo: bool = False
    ):
        """Create a new conversation"""
        conversation = Conversation(
            document_id=document_id,
            type=conversation_type,
            chunk_id=chunk_id,
            meta_data=meta_data or {},
            is_demo=is_demo
        )
        db.add(conversation)
        await db.flush()
        return conversation.to_dict()

    async def cleanup_demo_conversations(self, connection_id: str, db: AsyncSession):
        """Clean up demo conversations for a connection when it disconnects"""
        try:
            # Find all demo conversations created during this connection
            result = await db.execute(
                select(Conversation).where(
                    and_(
                        Conversation.is_demo == True,
                        Conversation.meta_data['connection_id'].astext == connection_id
                    )
                )
            )
            conversations = result.scalars().all()

            # Delete conversations and their messages
            for conversation in conversations:
                await db.delete(conversation)
            
            await db.commit()
        except Exception as e:
            logger.error(f"Error cleaning up demo conversations: {e}")

    def _cleanup_task(self, task):
      """Cleanup a single completed task"""
      try:
          self.active_tasks.discard(task)
          # Check if task failed with exception
          if task.exception():
              logger.error(f"Task failed with: {task.exception()}")
              # self.semaphore.release()
      except Exception as e:
          logger.error(f"Error cleaning up task: {e}")

    async def _cleanup_all_tasks(self):
        """Cleanup all active tasks when shutting down"""
        logger.info(f"Cleaning up {len(self.active_tasks)} active tasks")
        try:
            # Cancel all active tasks
            for task in self.active_tasks:
                task.cancel()
            
            # Wait for all tasks to complete
            if self.active_tasks:
                await asyncio.gather(*self.active_tasks, return_exceptions=True)
            
            # Clear active tasks set
            self.active_tasks.clear()
            
            # Reset semaphore 
            # while True:
            #     try:
            #         self.semaphore.release()
            #     except ValueError:
            #         break
        except Exception as e:
            logger.error(f"Error during task cleanup: {e}")