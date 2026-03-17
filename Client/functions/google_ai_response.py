from google import genai
from google.genai.types import Tool, GenerateContentConfig, GoogleSearch
import json
import os
import dotenv

dotenv.load_dotenv()
def get_google_ai_response(query: str) -> str:
    """
    Get AI-generated response using Google's Gemini model with search capability
    
    Args:
        search_content (str): The search query or content to process
        
    Returns:
        str: Combined response text
    """
    client = genai.Client(api_key=os.getenv("GOOGLE_AI_API_KEY"))
    model_id = "gemini-2.0-flash-exp"
    
    google_search_tool = Tool(
        google_search = GoogleSearch()
    )
    
    response = client.models.generate_content(
        model=model_id,
        contents=query,
        config=GenerateContentConfig(
            tools=[google_search_tool],
            response_modalities=["TEXT"],
            max_output_tokens=2048
        )
    )
    
    # Combine all text parts into a single string
    return json.dumps({'status': 'success', 'content': ' '.join(part.text for part in response.candidates[0].content.parts)}) # type: ignore
    # return response.candidates[0].__dict__

# Example usage:
# result = get_google_ai_response("What is the AQI of greater noida")
# print(result)