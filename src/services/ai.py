from typing import Dict, List, AsyncGenerator
import litellm
from src.core.config import settings
from src.core.logging import setup_logger

logger = setup_logger(__name__)

SYSTEM_PROMPT = """You are an AI assistant specialized in analyzing and discussing documents.
Your task is to:
1. Answer questions based on the document content provided
2. Maintain accuracy and cite specific parts when relevant
3. Be clear and concise in your responses
4. Admit when you're unsure or when information isn't in the document"""

QUESTION_PROMPT = """You are an AI assistant specialized in generating insightful questions about documents.
Your task is to:
1. Generate thought-provoking questions that explore key themes
2. Focus on critical analysis and understanding
3. Cover different aspects of the document
4. Ensure questions are specific to the document content"""

class AIService:
    def __init__(self):
        self.model = "gpt-4o"  # Using OpenAI instead of Gemini
        
    def _build_messages(self, messages: List[Dict], context: Dict) -> List[Dict]:
        """Build messages with context and system prompt"""
        document_content = context["document"]["content"]
        conversation_type = context["conversation"]["type"]
        
        # Add document context to system prompt
        system_context = f"""Document Title: {context['document']['title']}
Type: {conversation_type}
Content: {document_content}

{SYSTEM_PROMPT}"""
        
        full_messages = [
            {"role": "system", "content": system_context},
            *messages
        ]
        
        return full_messages
        
    async def stream_chat(
        self,
        messages: List[Dict],
        context: Dict
    ) -> AsyncGenerator[str, None]:
        """Stream chat responses with document context"""
        try:
            full_messages = self._build_messages(messages, context)
            
            response = await litellm.acompletion(
                model=self.model,
                messages=full_messages,
                stream=True,
                max_tokens=1000
            )
            
            async for chunk in response:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
                    
        except Exception as e:
            logger.error(f"Error in stream_chat: {str(e)}")
            yield f"I apologize, but I encountered an error: {str(e)}"
            
    async def generate_questions(self, document_content: str, count: int = 3) -> List[str]:
        """Generate questions about the document content"""
        try:
            prompt = f"""Based on the following document content, generate {count} insightful and specific questions:

Content: {document_content}

Requirements:
1. Generate exactly {count} questions
2. Questions should explore key themes and concepts
3. Focus on critical analysis and understanding
4. Make questions specific to this document's content
5. Format output as a simple list, one question per line

Questions:"""
            
            response = await litellm.acompletion(
                model=self.model,
                messages=[
                    {"role": "system", "content": QUESTION_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500
            )
            
            # Parse response into individual questions
            questions_text = response.choices[0].message.content
            questions = [
                q.strip().lstrip("123456789-.*) ")  # Remove any numbering or list markers
                for q in questions_text.split('\n')
                if q.strip() and not q.strip().isspace()
            ]
            
            logger.info(f"Generated {len(questions)} questions")
            return questions[:count]
            
        except Exception as e:
            logger.error(f"Error generating questions: {str(e)}")
            return [
                "What are the main themes of this document?",
                "Can you summarize the key points?",
                "What conclusions or recommendations are made?"
            ]