# backend/app/utils/ai.py
import boto3
from urllib.parse import urlparse
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain.prompts import PromptTemplate
from langchain_groq import ChatGroq
import chromadb
import os

def get_s3_file_data(s3_url: str) -> bytes:
    parsed_url = urlparse(s3_url)
    bucket = parsed_url.netloc.split(".")[0]
    key = parsed_url.path.lstrip("/")

    s3 = boto3.client("s3")
    response = s3.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()

# --- Step 1: Vector DB search function ---
def vector_db_search(query: str, top_k: int = 3):
    embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

    chroma_client = chromadb.HttpClient(host="chroma", port=8000)

    db = Chroma(
        client=chroma_client,
        collection_name="multimodal_documents_collection",
        embedding_function=embeddings
    )

    docs = db.similarity_search(query, k=top_k)
    return docs

# --- Step 2: Prompt builder function (with history) ---
def build_prompt(query: str, docs, history: list):
    context = "\n\n".join([doc.page_content for doc in docs])

    # format history as "User: ...\nAssistant: ..."
    formatted_history = ""
    for turn in history:
        role = turn.get("role", "user")
        text = turn.get("text", "")
        formatted_history += f"{role.capitalize()}: {text}\n"

    template = """
    You are a helpful assistant. 
    Use the following conversation history and provided context to answer the new user question.
    If the answer is not in the context, say you don't know.

    Conversation history:
    {history}

    Context:
    {context}

    Question: {question}

    Answer:
    """
    prompt = PromptTemplate(
        input_variables=["history", "question", "context"],
        template=template
    )
    return prompt.format(history=formatted_history, question=query, context=context)

# --- Step 3: LLM generate function ---
def llm_generate(prompt: str):
    llm = ChatGroq(
        model="mixtral-8x7b-32768",
        groq_api_key=os.getenv("OPENAI_API_KEY")
    )
    response = llm.invoke(prompt)
    return response.content

# --- Final pipeline ---
def rag_pipeline(user_query: str, history: list):
    docs = vector_db_search(user_query)
    prompt = build_prompt(user_query, docs, history)
    answer = llm_generate(prompt)
    return {"answer": answer}

# --- Main function called from routes ---
def generate_ai_response(query: str, history: list) -> str:
    if not query or query.strip() == "":
        return "Please provide a valid query."

    result = rag_pipeline(query, history)
    return result["answer"]
