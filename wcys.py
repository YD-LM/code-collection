import streamlit
import openai
from importlib.metadata import version

print("streamlit:", streamlit.__version__)
print("python-dotenv:", version("python-dotenv"))
print("openai:", openai.__version__)