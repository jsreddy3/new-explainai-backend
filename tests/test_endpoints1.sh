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

# Base URL
BASE_URL="http://localhost:8000/api"

# 1. Upload test document (needed for conversation tests)
echo "1. Uploading test document..."
UPLOAD_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/upload" \
  -H "accept: application/json" \
  -F "file=@tests/pdfs/pale fire presentation.pdf")

echo "Upload response: $UPLOAD_RESPONSE"
if handle_response "$UPLOAD_RESPONSE"; then
    DOCUMENT_ID=$(echo "$UPLOAD_RESPONSE" | jq -r '.document_id')
    echo "Document ID: $DOCUMENT_ID"
else
    exit 1
fi

# 2. Create main conversation
echo -e "\n2. Creating main conversation..."
CONV_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/$DOCUMENT_ID/conversations" \
  -H "Content-Type: application/json" \
  -d '{}')

echo "Main conversation response: $CONV_RESPONSE"
if handle_response "$CONV_RESPONSE"; then
    MAIN_CONV_ID=$(echo "$CONV_RESPONSE" | jq -r '.id')
    echo "Main conversation ID: $MAIN_CONV_ID"
else
    exit 1
fi

# 3. Generate initial questions
echo -e "\n3. Generating initial questions..."
QUESTIONS_RESPONSE=$(curl -s -X POST "$BASE_URL/conversations/$MAIN_CONV_ID/questions" \
  -H "Content-Type: application/json" \
  -d '{"count": 3}')

echo "Questions response:"
echo "$QUESTIONS_RESPONSE" | jq '.'

# 4. Start chat with first question
echo -e "\n4. Starting chat with first question..."
echo "First message:"
curl -N -X POST "$BASE_URL/conversations/$MAIN_CONV_ID/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "What is the main theme of this document?",
    "role": "user"
  }' | while read -r line; do
    if [[ $line == data:* ]]; then
        echo "${line#data: }"
    fi
done

# 5. Follow-up question
echo -e "\n5. Sending follow-up question..."
echo "Follow-up response:"
curl -N -X POST "$BASE_URL/conversations/$MAIN_CONV_ID/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "How does this relate to literary analysis?",
    "role": "user"
  }' | while read -r line; do
    if [[ $line == data:* ]]; then
        echo "${line#data: }"
    fi
done

# 6. Generate more specific questions
echo -e "\n6. Generating more specific questions..."
QUESTIONS_RESPONSE=$(curl -s -X POST "$BASE_URL/conversations/$MAIN_CONV_ID/questions" \
  -H "Content-Type: application/json" \
  -d '{
    "count": 2,
    "previous_questions": [
      "What is the main theme of this document?",
      "How does this relate to literary analysis?"
    ]
  }')

echo "Additional questions response:"
echo "$QUESTIONS_RESPONSE" | jq '.'