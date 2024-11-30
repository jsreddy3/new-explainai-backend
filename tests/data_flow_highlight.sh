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

# Function to encode text for JSON
json_escape() {
    printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
}

# Colors for better visibility
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Base URL
BASE_URL="http://localhost:8000/api"

echo -e "${BLUE}Starting highlight-aware question generation test...${NC}"

# 1. Upload test document
echo -e "\n1. Uploading test document..."
UPLOAD_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/upload" \
    -H "accept: application/json" \
    -F "file=@tests/pdfs/pale fire presentation.pdf")

if handle_response "$UPLOAD_RESPONSE"; then
    DOCUMENT_ID=$(echo "$UPLOAD_RESPONSE" | jq -r '.document_id')
    echo -e "${GREEN}Document uploaded successfully. ID: $DOCUMENT_ID${NC}"
else
    echo -e "${RED}Failed to upload document${NC}"
    exit 1
fi

# 2. Get first chunk
echo -e "\n2. Getting document chunks..."
DOC_RESPONSE=$(curl -s -X GET "$BASE_URL/documents/$DOCUMENT_ID" \
    -H "accept: application/json")

CHUNK_ID=$(echo "$DOC_RESPONSE" | jq -r '.chunks[0].id')
CHUNK_CONTENT=$(echo "$DOC_RESPONSE" | jq -r '.chunks[0].content')

echo "Got chunk ID: $CHUNK_ID"

# 3. Create conversation with specific highlight
echo -e "\n3. Creating chunk conversation with highlight..."
HIGHLIGHT_TEXT="This is a specific test highlight about Gradus"
HIGHLIGHT_START=0
HIGHLIGHT_LENGTH=100

CONV_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/$DOCUMENT_ID/conversations/chunk" \
    -H "Content-Type: application/json" \
    -d "{
        \"chunk_id\": \"$CHUNK_ID\",
        \"highlight_range\": [$HIGHLIGHT_START, $HIGHLIGHT_LENGTH],
        \"highlighted_text\": $(json_escape "$HIGHLIGHT_TEXT")
    }")

if handle_response "$CONV_RESPONSE"; then
    CONV_ID=$(echo "$CONV_RESPONSE" | jq -r '.id')
    echo -e "${GREEN}Conversation created successfully. ID: $CONV_ID${NC}"
else
    echo -e "${RED}Failed to create conversation${NC}"
    exit 1
fi

# 4. Verify conversation metadata
echo -e "\n4. Verifying conversation metadata..."
CONV_DETAILS=$(curl -s -X GET "$BASE_URL/documents/$DOCUMENT_ID/conversations" \
    -H "accept: application/json")

echo "Conversation metadata:"
echo "$CONV_DETAILS" | jq ".chunks[] | select(.id == \"$CONV_ID\")"

# 5. Generate initial questions
echo -e "\n5. Generating highlight-aware questions..."
QUESTIONS_RESPONSE=$(curl -s -X POST "$BASE_URL/conversations/$CONV_ID/questions" \
    -H "Content-Type: application/json" \
    -d '{
        "count": 3,
        "conversation_type": "chunk"
    }')

echo -e "\nInitial questions:"
echo "$QUESTIONS_RESPONSE" | jq '.'

# 6. Add some conversation context
echo -e "\n6. Adding conversation context..."
MESSAGE_RESPONSE=$(curl -s -X POST "$BASE_URL/conversations/$CONV_ID/messages" \
    -H "Content-Type: application/json" \
    -d '{
        "content": "What is the significance of this highlighted text?",
        "role": "user"
    }')

# 7. Generate questions with context
echo -e "\n7. Generating questions with conversation context..."
CONTEXT_QUESTIONS=$(curl -s -X POST "$BASE_URL/conversations/$CONV_ID/questions" \
    -H "Content-Type: application/json" \
    -d '{
        "count": 3,
        "conversation_type": "chunk",
        "previous_questions": ["What is the significance of this highlighted text?"]
    }')

echo -e "\nContext-aware questions:"
echo "$CONTEXT_QUESTIONS" | jq '.'

# 8. Verify data flow
echo -e "\n8. Checking data flow..."
echo -e "\n8.1 Conversation metadata:"
curl -s -X GET "$BASE_URL/conversations/$CONV_ID" \
    -H "accept: application/json" | jq '.meta_data'

echo -e "\n8.2 Question metadata:"
echo "$QUESTIONS_RESPONSE" | jq '.[0].meta_data'

# 9. Analysis
echo -e "\n${BLUE}Analysis:${NC}"
echo "Checking if questions reference highlight..."
echo "$QUESTIONS_RESPONSE" | jq -r '.[].content' | while read -r question; do
    if echo "$question" | grep -i -q "$HIGHLIGHT_TEXT"; then
        echo -e "${GREEN}✓ Question references highlight: $question${NC}"
    else
        echo -e "${RED}✗ Question may not reference highlight: $question${NC}"
    fi
done

# Output test summary
echo -e "\n${BLUE}Test Summary:${NC}"
echo "1. Document ID: $DOCUMENT_ID"
echo "2. Chunk ID: $CHUNK_ID"
echo "3. Conversation ID: $CONV_ID"
echo "4. Highlight Text: $HIGHLIGHT_TEXT"
echo "5. Highlight Range: [$HIGHLIGHT_START, $HIGHLIGHT_LENGTH]"