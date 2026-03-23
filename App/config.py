import os
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
load_dotenv(dotenv_path=env_path)

USER_NAME = os.getenv("USER_NAME", "Kartikey")
ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "AVA")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
