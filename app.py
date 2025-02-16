from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse,Response
import subprocess
import os,sys
import json
from pathlib import Path
import openai
import time
import logging
from config import *
import requests
from dotenv import load_dotenv
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI()

openai_api_chat  = "http://aiproxy.sanand.workers.dev/openai/v1/chat/completions" # for testing
openai_api_key = os.getenv("AIPROXY_TOKEN")

# OpenAI API Key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    logging.error("OPENAI_API_KEY is not set! Please set it as an environment variable.")
    raise ValueError("OPENAI_API_KEY is not set! Please set it as an environment variable.")

client = openai.OpenAI(api_key=OPENAI_API_KEY)

def install_dependencies(language, dependencies):
    """Install dependencies based on the language."""
    if not dependencies:
        logging.info("No dependencies to install.")
        return
    
    try:
        if language == "python":
            logging.info(f"Installing Python dependencies: {dependencies}")
            subprocess.run(["pip", "install"] + dependencies, check=True, capture_output=True)
        elif language == "node":
            logging.info(f"Installing Node.js dependencies: {dependencies}")
            subprocess.run(["npm", "install", "--global"] + dependencies, check=True, capture_output=True)
        elif language == "bash":
            logging.info(f"Installing Bash dependencies: {dependencies}")
            subprocess.run(["sudo", "apt-get", "install", "-y"] + dependencies, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"Dependency installation failed: {e.stderr.decode().strip()}")
        raise HTTPException(status_code=500, detail=f"Dependency installation failed: {e.stderr.decode().strip()}")

def get_fixed_code(task, code, error):
    """Send the error and current code to LLM for modification."""
    logging.info(f"Requesting LLM to fix code for task: {task}")
    
    new_prompt = (
        f"Task: {task}\n"
        f"Current Code:\n{code}\n"
        f"Error Encountered:\n{error}\n"
        f"Fix the code to resolve the error. Provide only the fixed JSON output."
    )

    response = get_response(new_prompt)
    
    try:
        fixed_code = json.loads(response.choices[0].message.content)
        logging.info("Received fixed code from LLM.")
        return fixed_code
    except json.JSONDecodeError as e:
        logging.error(f"Error parsing LLM response: {e}")
        raise HTTPException(status_code=500, detail="Error parsing LLM response")

def execute_code(llmcode):
    """Execute code in the appropriate environment."""
    language = llmcode.get("language", "").lower()
    code = llmcode.get("code", "")

    dependencies = llmcode.get("python_dependencies", []) if language == "python" else []
    install_dependencies(language, dependencies)

    if language == "python":
        cmd = [sys.executable, "-c", code]  # Ensure correct Python path
    elif language == "bash":
        cmd = ["bash", "-c", code]
    elif language == "node":
        cmd = ["node", "-e", code]
    else:
        logging.error(f"Unsupported language: {language}")
        return False, f"Unsupported language: {language}"

    try:
        result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.stderr.strip():  # If there's an error in stderr, return failure
            logging.error(f"Execution error ({language}): {result.stderr.strip()}")
            return False, result.stderr.strip()

        logging.info(f"Code execution succeeded for {language}: {result.stdout.strip()}")
        return True, result.stdout.strip()

    except subprocess.CalledProcessError as e:
        logging.error(f"Execution error ({language}): {e.stderr.strip()}")
        return False, e.stderr.strip()

def run_task_fix(task, llm_ouput, max_retries=1):
    """Try executing the task, retrying with LLM fixes if errors occur."""
    try:
        attempt = 0
        while attempt <= max_retries:
            logging.info(f"Attempt {attempt + 1}/{max_retries} for task: {task}")
            
            # Ensure llmcode is a valid dictionary
            if isinstance(llm_ouput, str):
                try:
                    llm_ouput = json.loads(llm_ouput)  # Convert string to dictionary if needed
                except json.JSONDecodeError:
                    logging.error("Invalid JSON format in LLM response")
                    return False
            
            success, error = execute_code(llm_ouput)
            if success:
                logging.info(f"Task '{task}' executed successfully.")
                return True
            
            time.sleep(1)  # Small delay before retrying
            logging.info(f"Retrying with modified code for task: {task}")
            # llm_ouput = get_fixed_code(task, llm_ouput["code"], error)
            # attempt += 1
    except Exception as e:
        wait_time = (2 ** attempt) * 5  # Exponential backoff
        logging.warning(f"Rate limit reached. Retrying in {wait_time} seconds...")
        time.sleep(wait_time)

    logging.error(f"Task '{task}' failed after {max_retries} retries.")
    return False


def get_llm_response(task: str):
    """Parses task description using GPT-4o-mini and selects the best tool functions."""
    try:
        response = requests.post(
            openai_api_chat,
            headers={"Authorization": f"Bearer {openai_api_key}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT3 },
                    {"role": "user", "content": task }
                ],
            },
            timeout=10
        )
        return response.json()["choices"][0]["message"]["content"]

    except Exception as e:
        logging.error(f"OpenAI API error: {e}")
        return Response(status_code=500)


@app.post("/run")
async def run(task: str):
    """Handle task execution request."""
    try:
        logging.info(f"Received task request: {task}")
        gpt_answer_json = get_llm_response(task)
        print("Gpt: ",gpt_answer_json)
        success = run_task_fix(task, gpt_answer_json, max_retries=1)
        
        status = "success" if success else "failure"
        return {"status": status}
    except KeyError as e:
        logging.error(f"Key error: {e}")
        raise HTTPException(status_code=400, detail=f"Key error: {e}")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")


@app.get("/read", response_class=PlainTextResponse)
async def read_file(path: str = Query(..., description="Path to the file to read")):
    if (not os.path.exists(path)) or (not os.path.isfile(path)):
        return Response(status_code=404)  # Empty body for 404
    try:
        with open(path, 'r') as f:
            content = f.read()
        return JSONResponse(content=content,status_code=200) 
    
    except PermissionError:
        return Response(status_code=403)
    except Exception as e:
        return Response(status_code=500)


@app.get("/")
def home():
    """Home endpoint."""
    return {"message": "Automation Agent for Project 1 Tasks"}

if __name__ == "__main__":
    logging.info("Starting FastAPI application...")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
