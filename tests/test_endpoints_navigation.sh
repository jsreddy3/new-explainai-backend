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

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Test counter
test_count=0
passed_count=0

run_test() {
    local test_name=$1
    local result=$2
    
    test_count=$((test_count + 1))
    
    if [ $result -eq 0 ]; then
        passed_count=$((passed_count + 1))
        echo -e "${GREEN}✓ $test_name passed${NC}"
    else
        echo -e "${RED}✗ $test_name failed${NC}"
    fi
    echo "----------------------------------------"
}

echo "Starting Navigation Flow Tests..."
echo "=========================================="

# 1. Upload Document and verify new response format
echo "1. Testing document upload with new response format..."
UPLOAD_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/upload" \
  -H "accept: application/json" \
  -F "file=@tests/pdfs/pale fire presentation.pdf")

echo "Upload response:"
echo "$UPLOAD_RESPONSE" | jq '.'

# Verify response structure
if echo "$UPLOAD_RESPONSE" | jq -e '.document_id and .main_conversation_id and .current_chunk' > /dev/null; then
    run_test "Document Upload Response Format" 0
    
    # Store IDs for further tests
    DOCUMENT_ID=$(echo "$UPLOAD_RESPONSE" | jq -r '.document_id')
    CONVERSATION_ID=$(echo "$UPLOAD_RESPONSE" | jq -r '.main_conversation_id')
    CHUNK_ID=$(echo "$UPLOAD_RESPONSE" | jq -r '.current_chunk.id')
    NEXT_CHUNK_ID=$(echo "$UPLOAD_RESPONSE" | jq -r '.current_chunk.navigation.next')
else
    run_test "Document Upload Response Format" 1
    exit 1
fi

# 2. Test chunk navigation - first chunk
echo "2. Testing navigation from first chunk..."
NAV_RESPONSE=$(curl -s -X GET "$BASE_URL/documents/$DOCUMENT_ID/chunks/$CHUNK_ID/navigate" \
  -H "accept: application/json")

echo "Navigation response (first chunk):"
echo "$NAV_RESPONSE" | jq '.'

# Verify first chunk navigation
if echo "$NAV_RESPONSE" | jq -e '.current and .navigation and .navigation.prev == null' > /dev/null; then
    run_test "First Chunk Navigation" 0
else
    run_test "First Chunk Navigation" 1
fi

# 3. Test navigation to next chunk
echo "3. Testing navigation to next chunk..."
if [ "$NEXT_CHUNK_ID" != "null" ]; then
    NEXT_NAV_RESPONSE=$(curl -s -X GET "$BASE_URL/documents/$DOCUMENT_ID/chunks/$NEXT_CHUNK_ID/navigate" \
      -H "accept: application/json")
    
    echo "Navigation response (next chunk):"
    echo "$NEXT_NAV_RESPONSE" | jq '.'
    
    # Verify next chunk navigation
    if echo "$NEXT_NAV_RESPONSE" | jq -e '.current and .navigation and .navigation.prev' > /dev/null; then
        run_test "Next Chunk Navigation" 0
    else
        run_test "Next Chunk Navigation" 1
    fi
else
    echo "No next chunk available, skipping next chunk navigation test"
fi

# 4. Test invalid chunk navigation
echo "4. Testing navigation with invalid chunk ID..."
INVALID_NAV_RESPONSE=$(curl -s -X GET "$BASE_URL/documents/$DOCUMENT_ID/chunks/invalid-id/navigate" \
  -H "accept: application/json")

echo "Invalid navigation response:"
echo "$INVALID_NAV_RESPONSE" | jq '.'

# Verify error handling
if echo "$INVALID_NAV_RESPONSE" | jq -e '.detail == "Chunk not found"' > /dev/null; then
    run_test "Invalid Chunk Navigation" 0
else
    run_test "Invalid Chunk Navigation" 1
fi

# 5. Test invalid document navigation
echo "5. Testing navigation with invalid document ID..."
INVALID_DOC_RESPONSE=$(curl -s -X GET "$BASE_URL/documents/invalid-id/chunks/$CHUNK_ID/navigate" \
  -H "accept: application/json")

echo "Invalid document response:"
echo "$INVALID_DOC_RESPONSE" | jq '.'

# Verify error handling
if echo "$INVALID_DOC_RESPONSE" | jq -e '.detail == "Document has no chunks"' > /dev/null; then
    run_test "Invalid Document Navigation" 0
else
    run_test "Invalid Document Navigation" 1
fi

# Print summary
echo "=========================================="
echo "Test Summary:"
echo "Total tests: $test_count"
echo "Passed: $passed_count"
echo "Failed: $((test_count - passed_count))"
echo "=========================================="

# Exit with failure if any tests failed
[ $passed_count -eq $test_count ]
