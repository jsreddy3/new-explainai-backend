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

# 1. Upload Document
echo "1. Uploading document..."
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

# 2. Get document details
echo -e "\n2. Getting document details..."
DOC_RESPONSE=$(curl -s -X GET "$BASE_URL/documents/$DOCUMENT_ID" \
  -H "accept: application/json")

echo "Document details:"
echo "$DOC_RESPONSE" | jq '.'

# 3. Try uploading invalid file
echo -e "\n3. Testing invalid file upload..."
INVALID_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/upload" \
  -H "accept: application/json" \
  -F "file=@tests/test_endpoints0.sh")

echo "Invalid upload response:"
echo "$INVALID_RESPONSE" | jq '.'

# 4. Upload large file (if available)
echo -e "\n4. Testing large file upload..."
if [ -f "tests/pdfs/large_file.pdf" ]; then
    LARGE_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/upload" \
      -H "accept: application/json" \
      -F "file=@tests/pdfs/large_file.pdf")
    
    echo "Large file upload response:"
    echo "$LARGE_RESPONSE" | jq '.'
else
    echo "Skipping large file test (file not available)"
fi

# 5. Get non-existent document
echo -e "\n5. Testing non-existent document retrieval..."
NONEXISTENT_RESPONSE=$(curl -s -X GET "$BASE_URL/documents/nonexistent-id" \
  -H "accept: application/json")

echo "Non-existent document response:"
echo "$NONEXISTENT_RESPONSE" | jq '.'

# 6. Get document chunks
echo -e "\n6. Getting document chunks..."
CHUNKS_RESPONSE=$(curl -s -X GET "$BASE_URL/documents/$DOCUMENT_ID/chunks" \
  -H "accept: application/json")

echo "Chunks response:"
echo "$CHUNKS_RESPONSE" | jq '.'