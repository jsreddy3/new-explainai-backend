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

# Base URL
BASE_URL="http://localhost:8000/api"

# 1. Setup: Upload document
echo "1. Setting up test document..."
UPLOAD_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/upload" \
    -H "accept: application/json" \
    -F "file=@tests/pdfs/pale fire presentation.pdf")

if handle_response "$UPLOAD_RESPONSE"; then
    DOCUMENT_ID=$(echo "$UPLOAD_RESPONSE" | jq -r '.document_id')
    echo "Document ID: $DOCUMENT_ID"
else
    exit 1
fi

# 2. Get first chunk content
echo -e "\n2. Getting document chunks..."
DOC_RESPONSE=$(curl -s -X GET "$BASE_URL/documents/$DOCUMENT_ID" \
    -H "accept: application/json")
CHUNK_ID=$(echo "$DOC_RESPONSE" | jq -r '.chunks[0].id')
CHUNK_CONTENT=$(echo "$DOC_RESPONSE" | jq -r '.chunks[0].content')

# 3. Test meaningful highlight conversations
echo -e "\n3. Testing highlight-aware conversations..."

# Create conversation with meaningful highlight
echo "3.1 Creating conversation with highlight of key concept..."
HIGHLIGHT_START=0
HIGHLIGHT_LENGTH=100
HIGHLIGHTED_TEXT=$(echo "${CHUNK_CONTENT:$HIGHLIGHT_START:$HIGHLIGHT_LENGTH}" | tr -d '\n\r')
HIGHLIGHTED_TEXT_JSON=$(json_escape "$HIGHLIGHTED_TEXT")

# Debug output
echo "Chunk ID: $CHUNK_ID"
echo "Highlight range: [$HIGHLIGHT_START, $((HIGHLIGHT_START + HIGHLIGHT_LENGTH))]"
echo "Highlighted text length: ${#HIGHLIGHTED_TEXT}"

REQUEST_BODY=$(cat <<EOF
{
    "chunk_id": "$CHUNK_ID",
    "highlight_range": [$HIGHLIGHT_START, $((HIGHLIGHT_START + HIGHLIGHT_LENGTH))],
    "highlighted_text": ${HIGHLIGHTED_TEXT_JSON}
}
EOF
)

echo "Request body:"
echo "$REQUEST_BODY" | jq '.'

CHUNK_CONV_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/$DOCUMENT_ID/conversations/chunk" \
    -H "Content-Type: application/json" \
    -d "$REQUEST_BODY")

if handle_response "$CHUNK_CONV_RESPONSE"; then
    CONV_ID=$(echo "$CHUNK_CONV_RESPONSE" | jq -r '.id')
    echo "Conversation ID: $CONV_ID"
else
    echo "Error response:"
    echo "$CHUNK_CONV_RESPONSE"
    exit 1
fi

# 4. Test highlight-aware question generation
echo -e "\n4. Testing highlight-aware question generation..."

# Generate initial questions about highlight
echo "4.1 Generating highlight-specific questions..."
INITIAL_QUESTIONS=$(curl -s -X POST "$BASE_URL/conversations/$CONV_ID/questions" \
    -H "Content-Type: application/json" \
    -d '{
        "count": 3,
        "conversation_type": "chunk",
        "previous_questions": []
    }')

echo "Initial highlight-specific questions:"
echo "$INITIAL_QUESTIONS" | jq '.'

# 5. Test contextual conversation about highlight
echo -e "\n5. Testing contextual conversation about highlight..."

# Start conversation about highlight
echo "5.1 Starting conversation about highlighted text..."
CHAT_RESPONSE=$(curl -N -X POST "$BASE_URL/conversations/$CONV_ID/chat" \
    -H "Content-Type: application/json" \
    -d '{
        "content": "What is the significance of this highlighted passage?",
        "role": "user"
    }')

echo "Initial chat response:"
echo "$CHAT_RESPONSE"

# 6. Test question generation after context
echo -e "\n6. Testing question generation with conversation context..."

# Get questions for conversation
echo "6.1 Getting all generated questions..."
QUESTIONS_RESPONSE=$(curl -s -X GET "$BASE_URL/conversations/$CONV_ID/questions" \
    -H "accept: application/json")
echo "All generated questions:"
echo "$QUESTIONS_RESPONSE" | jq '.'

# Generate follow-up questions considering context
echo "6.2 Generating context-aware follow-up questions..."
FOLLOWUP_QUESTIONS=$(curl -s -X POST "$BASE_URL/conversations/$CONV_ID/questions" \
    -H "Content-Type: application/json" \
    -d '{
        "count": 2,
        "conversation_type": "chunk",
        "previous_questions": ["What is the significance of this highlighted passage?"]
    }')

echo "Follow-up questions considering context:"
echo "$FOLLOWUP_QUESTIONS" | jq '.'

# 7. Verify highlight awareness
echo -e "\n7. Verifying highlight awareness..."

# Get conversation details to verify highlight storage
echo "7.1 Getting conversation details..."
CONV_DETAILS=$(curl -s -X GET "$BASE_URL/documents/$DOCUMENT_ID/conversations" \
    -H "accept: application/json")

# Check highlight metadata
echo "7.2 Checking highlight metadata in conversation..."
echo "Conversation details:"
echo "$CONV_DETAILS" | jq '.'