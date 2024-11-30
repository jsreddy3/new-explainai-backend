#!/bin/bash

# Function to check if jq is installed
if ! command -v jq &> /dev/null; then
    echo "jq is required but not installed. Please install jq first."
    exit 1
fi

# Function to handle errors
handle_response() {
    if [[ $(echo "$1" | jq -r '.detail' 2>/dev/null) != "null" ]]; then
        echo "Error: $(echo "$1" | jq -r '.detail')"
        return 1
    fi
    return 0
}

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

# Base URL
BASE_URL="http://localhost:8000/api"

echo -e "${BLUE}Starting comprehensive summarization and merge tests...${NC}\n"

# 1. Upload test document
echo "1. Setting up test document..."
UPLOAD_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/upload" \
    -H "accept: application/json" \
    -H "Accept: application/json" \
    -F "file=@tests/pdfs/pale fire presentation.pdf")

if handle_response "$UPLOAD_RESPONSE"; then
    DOCUMENT_ID=$(echo "$UPLOAD_RESPONSE" | jq -r '.document_id')
    echo -e "${GREEN}Document uploaded successfully. ID: $DOCUMENT_ID${NC}"
else
    exit 1
fi

# 2. Create main conversation
echo -e "\n2. Creating main conversation..."
MAIN_CONV_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/$DOCUMENT_ID/conversations" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -d '{}')

if handle_response "$MAIN_CONV_RESPONSE"; then
    MAIN_CONV_ID=$(echo "$MAIN_CONV_RESPONSE" | jq -r '.id')
    echo -e "${GREEN}Main conversation created. ID: $MAIN_CONV_ID${NC}"
else
    exit 1
fi

# 3. Create chunk conversation with highlight
echo -e "\n3. Creating chunk conversation with highlight..."
CHUNK_RESPONSE=$(curl -s -X GET "$BASE_URL/documents/$DOCUMENT_ID/chunks" \
    -H "Accept: application/json")
FIRST_CHUNK_ID=$(echo "$CHUNK_RESPONSE" | jq -r '.[0].id')
HIGHLIGHTED_TEXT="Mathematical unprojection requires a pre-established code or framework shared between the producer and the user. This framework is analogous to the shared understanding necessary for successful literary interpretation."

CHUNK_CONV_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/$DOCUMENT_ID/conversations/chunk" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -d "{\"chunk_id\": \"$FIRST_CHUNK_ID\", \"highlight_range\": [0, 100], \"highlighted_text\": \"$HIGHLIGHTED_TEXT\"}")

if handle_response "$CHUNK_CONV_RESPONSE"; then
    CHUNK_CONV_ID=$(echo "$CHUNK_CONV_RESPONSE" | jq -r '.id')
    echo -e "${GREEN}Chunk conversation created. ID: $CHUNK_CONV_ID${NC}"
else
    exit 1
fi

# 4. Have a conversation in the chunk conversation
echo -e "\n4. Having a conversation about the highlighted text..."

# User asks about the concept
echo "4.1 User asks about the concept"
MESSAGE1_RESPONSE=$(curl -s -X POST "$BASE_URL/conversations/$CHUNK_CONV_ID/chat" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -d '{
        "content": "Can you explain what mathematical unprojection means in this context and why it requires a shared framework?",
        "role": "user"
    }')

echo "User message sent. AI responding..."
echo "$MESSAGE1_RESPONSE" | jq '.'

# User asks for a practical example
echo -e "\n4.2 User asks for a practical example"
MESSAGE2_RESPONSE=$(curl -s -X POST "$BASE_URL/conversations/$CHUNK_CONV_ID/chat" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -d '{
        "content": "Can you give a concrete example of how this shared framework works in literary interpretation?",
        "role": "user"
    }')

echo "User message sent. AI responding..."
echo "$MESSAGE2_RESPONSE" | jq '.'

# 5. Merge the chunk conversation into main
echo -e "\n5. Merging chunk conversation into main conversation..."
MERGE_RESPONSE=$(curl -s -X POST "$BASE_URL/conversations/$MAIN_CONV_ID/merge" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -d "{
        \"highlight_conversation_id\": \"$CHUNK_CONV_ID\"
    }")

if handle_response "$MERGE_RESPONSE"; then
    echo -e "${GREEN}Conversations merged successfully${NC}"
    echo "Merge summary:"
    echo "$MERGE_RESPONSE" | jq '.'
else
    exit 1
fi

# 6. Have a follow-up conversation in main that references the merged content
echo -e "\n6. Having a follow-up conversation in main conversation..."
FOLLOWUP_RESPONSE=$(curl -s -X POST "$BASE_URL/conversations/$MAIN_CONV_ID/chat" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -d '{
        "content": "Based on our previous discussion about mathematical unprojection and shared frameworks, how does this concept apply to the broader themes in Pale Fire?",
        "role": "user"
    }')

echo "Follow-up conversation:"
echo "$FOLLOWUP_RESPONSE" | jq '.'

# 7. Verify the conversation history and structure
echo -e "\n7. Verifying conversation structure..."
MAIN_CONV_DETAILS=$(curl -s -X GET "$BASE_URL/conversations/$MAIN_CONV_ID" \
    -H "Accept: application/json")
echo "Main conversation details:"
echo "$MAIN_CONV_DETAILS" | jq '.'

echo -e "\n${GREEN}All tests completed successfully!${NC}"