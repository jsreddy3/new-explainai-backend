from src.services.pdf import PDFService
import asyncio

async def test_extraction():
    url = "https://ssi.inc"
    service = PDFService()
    content = await service.extract_web_content(url)
    print("Extracted content length:", len(content))
    print("\nFirst 500 characters:")
    print(content[:500])
    return content

# Run with:
asyncio.run(test_extraction())