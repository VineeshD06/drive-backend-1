import os
import io
import pandas as pd
import boto3
from PIL import Image
import pytesseract
import chromadb

from transformers import pipeline
from langchain_community.document_loaders import (
    PyPDFLoader,
    Docx2txtLoader,
    TextLoader,
    UnstructuredPowerPointLoader,
)
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document

# ------------------------
# S3 CONFIG
# ------------------------
S3_BUCKET = os.getenv("S3_BUCKET", "your-bucket-name")
S3_PREFIX = os.getenv("S3_PREFIX", "documents/")  # folder inside S3 bucket
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")

s3_client = boto3.client("s3", region_name=AWS_REGION)


def parse_spreadsheet_from_bytes(file_bytes: bytes, ext: str) -> str:
    """Parse CSV/XLSX from raw bytes."""
    try:
        if ext == ".csv":
            df = pd.read_csv(io.BytesIO(file_bytes))
        else:
            df = pd.read_excel(io.BytesIO(file_bytes))

        row_strings = []
        for _, row in df.iterrows():
            row_string = ", ".join([f"{col}: {val}" for col, val in row.items() if pd.notna(val)])
            row_strings.append(row_string)

        return "\n".join(row_strings)
    except Exception as e:
        print(f"Error parsing spreadsheet: {e}")
        return ""


def process_image_with_pipeline_bytes(file_bytes: bytes, ext: str, captioner) -> str:
    """Run captioning + OCR on image bytes."""
    try:
        # Save temporarily to pass to captioner (pipeline expects path or PIL)
        img = Image.open(io.BytesIO(file_bytes))
        img_path = f"/tmp/temp_image{ext}"
        img.save(img_path)

        caption_result = captioner(img_path)
        caption = caption_result[0]["generated_text"]
    except Exception as e:
        caption = f"Caption generation failed: {e}"

    try:
        ocr_text = pytesseract.image_to_string(img)
    except Exception as e:
        ocr_text = f"OCR failed: {e}"

    return f"Image Caption: {caption}\n\nExtracted Text (OCR):\n{ocr_text}"


def load_document_from_s3(key: str, captioner):
    """Download file from S3 and parse it into LangChain Documents."""
    ext = os.path.splitext(key)[1].lower()
    obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
    file_bytes = obj["Body"].read()

    if ext == ".pdf":
        tmp = f"/tmp/{os.path.basename(key)}"
        with open(tmp, "wb") as f:
            f.write(file_bytes)
        return PyPDFLoader(tmp).load()

    elif ext == ".docx":
        tmp = f"/tmp/{os.path.basename(key)}"
        with open(tmp, "wb") as f:
            f.write(file_bytes)
        return Docx2txtLoader(tmp).load()

    elif ext == ".pptx":
        tmp = f"/tmp/{os.path.basename(key)}"
        with open(tmp, "wb") as f:
            f.write(file_bytes)
        return UnstructuredPowerPointLoader(tmp).load()

    elif ext == ".txt":
        return [Document(page_content=file_bytes.decode("utf-8"), metadata={"source": key})]

    elif ext in [".csv", ".xlsx"]:
        content = parse_spreadsheet_from_bytes(file_bytes, ext)
        if content:
            return [Document(page_content=content, metadata={"source": key})]

    elif ext in [".jpg", ".jpeg", ".png"]:
        content = process_image_with_pipeline_bytes(file_bytes, ext, captioner)
        if content:
            return [Document(page_content=content, metadata={"source": key})]

    else:
        print(f"Skipping unsupported file type: {ext}")
        return []


def main():
    CHROMA_HOST = os.getenv("CHROMA_HOST", "chromadb_server")
    CHROMA_PORT = int(os.getenv("CHROMA_PORT", 8000))
    COLLECTION_NAME = "multimodal_documents_collection"

    captioner = pipeline("image-to-text", model="Salesforce/blip-image-captioning-large")
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    embedding_model = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

    chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    vector_db = Chroma(client=chroma_client, collection_name=COLLECTION_NAME, embedding_function=embedding_model)

    existing_items = vector_db.get(include=["metadatas"])
    existing_sources = set(item["source"] for item in existing_items["metadatas"])

    response = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_PREFIX)
    if "Contents" not in response:
        print("No files found in S3 bucket.")
        return

    files_to_process = [obj["Key"] for obj in response["Contents"] if obj["Key"] not in existing_sources]
    if not files_to_process:
        print("No new files to process. ✅")
        return

    all_chunks = []
    for key in files_to_process:
        documents = load_document_from_s3(key, captioner)
        if not documents: continue
        all_chunks.extend(text_splitter.split_documents(documents))

    if all_chunks:
        vector_db.add_documents(all_chunks)
        print(f"✅ {len(all_chunks)} chunks added to ChromaDB")

if __name__ == "__main__":
    main()