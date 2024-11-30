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

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

# Base URL
BASE_URL="http://localhost:8000/api"

echo -e "${BLUE}Starting comprehensive question generation tests...${NC}\n"

# 1. Setup: Upload test document
echo "1. Setting up test document..."
UPLOAD_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/upload" \
    -H "accept: application/json" \
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
    -d '{}')

if handle_response "$MAIN_CONV_RESPONSE"; then
    MAIN_CONV_ID=$(echo "$MAIN_CONV_RESPONSE" | jq -r '.id')
    echo -e "${GREEN}Main conversation created. ID: $MAIN_CONV_ID${NC}"
else
    exit 1
fi

# 3. Get first chunk and create chunk conversation
echo -e "\n3. Setting up chunk conversation..."
DOC_RESPONSE=$(curl -s -X GET "$BASE_URL/documents/$DOCUMENT_ID" \
    -H "accept: application/json")
CHUNK_ID=$(echo "$DOC_RESPONSE" | jq -r '.chunks[0].id')
CHUNK_CONTENT=$(echo "$DOC_RESPONSE" | jq -r '.chunks[0].content')

# Create conversation with meaningful highlight
HIGHLIGHT_START=0
HIGHLIGHT_LENGTH=100
HIGHLIGHTED_TEXT=$(echo "${CHUNK_CONTENT:$HIGHLIGHT_START:$HIGHLIGHT_LENGTH}" | tr -d '\n\r')
HIGHLIGHTED_TEXT_JSON=$(json_escape "$HIGHLIGHTED_TEXT")

CHUNK_CONV_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/$DOCUMENT_ID/conversations/chunk" \
    -H "Content-Type: application/json" \
    -d "{
        \"chunk_id\": \"$CHUNK_ID\",
        \"highlight_range\": [$HIGHLIGHT_START, $((HIGHLIGHT_START + HIGHLIGHT_LENGTH))],
        \"highlighted_text\": ${HIGHLIGHTED_TEXT_JSON}
    }")

if handle_response "$CHUNK_CONV_RESPONSE"; then
    CHUNK_CONV_ID=$(echo "$CHUNK_CONV_RESPONSE" | jq -r '.id')
    echo -e "${GREEN}Chunk conversation created. ID: $CHUNK_CONV_ID${NC}"
else
    exit 1
fi

# 4. Test Main Conversation Questions
echo -e "\n${BLUE}4. Testing main conversation question generation...${NC}"

# 4.1 Initial questions (document-aware)
echo "4.1 Testing initial document-aware questions..."
INITIAL_QUESTIONS=$(curl -s -X POST "$BASE_URL/conversations/$MAIN_CONV_ID/questions" \
    -H "Content-Type: application/json" \
    -d '{
        "count": 3,
        "conversation_type": "main"
    }')

echo "Initial document-aware questions:"
echo "$INITIAL_QUESTIONS" | jq '.'

# 4.2 Add context through conversation
echo -e "\n4.2 Adding conversation context..."
curl -s -X POST "$BASE_URL/conversations/$MAIN_CONV_ID/messages" \
    -H "Content-Type: application/json" \
    -d '{
        "content": "How does projection theory relate to literary interpretation?",
        "role": "user"
    }'

# 4.3 Questions with conversation context
echo -e "\n4.3 Testing questions with conversation context..."
CONTEXT_QUESTIONS=$(curl -s -X POST "$BASE_URL/conversations/$MAIN_CONV_ID/questions" \
    -H "Content-Type: application/json" \
    -d '{
        "count": 3,
        "conversation_type": "main",
        "previous_questions": ["How does projection theory relate to literary interpretation?"]
    }')

echo "Context-aware questions:"
echo "$CONTEXT_QUESTIONS" | jq '.'

# 5. Test Chunk Conversation Questions
echo -e "\n${BLUE}5. Testing chunk conversation question generation...${NC}"

# 5.1 Initial highlight-aware questions
echo "5.1 Testing initial highlight-aware questions..."
HIGHLIGHT_QUESTIONS=$(curl -s -X POST "$BASE_URL/conversations/$CHUNK_CONV_ID/questions" \
    -H "Content-Type: application/json" \
    -d "{
        \"count\": 3,
        \"conversation_type\": \"chunk\",
        \"highlight_text\": ${HIGHLIGHTED_TEXT_JSON}
    }")

echo "Initial highlight-aware questions:"
echo "$HIGHLIGHT_QUESTIONS" | jq '.'

# 5.2 Add chunk-specific context
echo -e "\n5.2 Adding chunk-specific conversation context..."
curl -s -X POST "$BASE_URL/conversations/$CHUNK_CONV_ID/messages" \
    -H "Content-Type: application/json" \
    -d '{
        "content": "What specific aspects of Gradus are being analyzed through projection theory?",
        "role": "user"
    }'

# 5.3 Questions with highlight and conversation context
echo -e "\n5.3 Testing questions with highlight and conversation context..."
CHUNK_QUESTIONS=$(curl -s -X POST "$BASE_URL/conversations/$CHUNK_CONV_ID/questions" \
    -H "Content-Type: application/json" \
    -d "{
        \"count\": 3,
        \"conversation_type\": \"chunk\",
        \"highlight_text\": ${HIGHLIGHTED_TEXT_JSON},
        \"previous_questions\": [\"What specific aspects of Gradus are being analyzed through projection theory?\"]
    }")

echo "Full context questions:"
echo "$CHUNK_QUESTIONS" | jq '.'

# 6. Verify Question Storage and Metadata
echo -e "\n${BLUE}6. Verifying question storage and metadata...${NC}"
STORED_QUESTIONS=$(curl -s -X GET "$BASE_URL/conversations/$MAIN_CONV_ID/questions" \
    -H "accept: application/json")

echo "Stored questions:"
echo "$STORED_QUESTIONS" | jq '.'

# Function to analyze questions for specific content
analyze_questions() {
    local questions="$1"
    local search_term="$2"
    local found=false
    
    while IFS= read -r question; do
        if [[ "$question" == *"$search_term"* ]]; then
            found=true
            break
        fi
    done < <(echo "$questions" | jq -r '.[].content')
    
    if $found; then
        echo -e "${GREEN}✓ Questions reference $search_term${NC}"
    else
        echo -e "${RED}✗ Questions do not reference $search_term${NC}"
    fi
}

# Function to validate highlight-aware questions
validate_highlight_questions() {
    local questions="$1"
    local highlight="$2"
    
    echo "Validating highlight-aware questions..."
    if [[ $(echo "$questions" | jq -r '.[].content' | grep -i "$highlight") ]]; then
        echo -e "${GREEN}✓ Questions reference highlighted text${NC}"
    else
        echo -e "${RED}✗ Questions do not reference highlighted text${NC}"
    fi
}

# 7. Analysis
echo -e "\n${BLUE}7. Analyzing question generation...${NC}"

echo "7.1 Analyzing document awareness..."
analyze_questions "$INITIAL_QUESTIONS" "Pale Fire"

# Validate highlight awareness
echo -e "\n7.2 Analyzing highlight awareness..."
validate_highlight_questions "$CHUNK_QUESTIONS" "$HIGHLIGHTED_TEXT"

echo -e "\n7.3 Analyzing conversation context awareness..."
validate_highlight_questions "$CONTEXT_QUESTIONS" "projection theory"

echo -e "\n${GREEN}Question generation tests completed!${NC}"