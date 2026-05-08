import json
import threading
from pathlib import Path

CONVERSATIONS = Path("./Server/data/conversations")  # folder containing json files
LAST_CHECK_FILE = Path("./Server/data/memories/last_checked.txt")


def load_last_timestamp():
    if LAST_CHECK_FILE.exists():
        return LAST_CHECK_FILE.read_text().strip()
    return "00000000_000000"  # default old timestamp


def save_last_timestamp(timestamp):
    LAST_CHECK_FILE.write_text(timestamp)


def clean_conversation(data):
    """
    Keeps only user + assistant messages.
    Removes summary/system/topics/etc.
    """

    cleaned_messages = []

    for msg in data.get("messages", []):
        if msg.get("role") in ["user", "assistant"]:
            cleaned_messages.append(
                {
                    "role": msg["role"],
                    "content": msg["content"],
                    "timestamp": msg.get("timestamp"),
                }
            )

    return {"id": data.get("id"), "messages": cleaned_messages}


def process_new_memories():
    last_timestamp = load_last_timestamp()

    # get all json files sorted
    files = sorted(f for f in CONVERSATIONS.glob("*.json") if f.name != "index.json")
    cleaned_conversations = []
    newest_timestamp = last_timestamp

    for file in files:
        file_timestamp = file.stem  # filename without .json

        # skip already processed files
        if file_timestamp <= last_timestamp:
            continue

        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)

            cleaned = clean_conversation(data)
            cleaned_conversations.append(cleaned)

            # update newest timestamp
            if file_timestamp > newest_timestamp:
                newest_timestamp = file_timestamp

        except Exception as e:
            print(f"Error processing {file.name}: {e}")

    # save newest checked timestamp
    save_last_timestamp(newest_timestamp)

    # return as string
    return json.dumps(cleaned_conversations, ensure_ascii=False, indent=2)


def get_memories_and_behaviours():
    with open(Path("./Server/data/memories/memories.txt"), "r") as file:
        data = file.read()
        with open(Path("./Server/data/memories/behaviours.txt"), "r") as f:
            behaviours = f.read()
            f.close()
        file.close()
        return data, behaviours


def write_memories_and_behaviours(memories, behaviours):
    with open(Path("./Server/data/memories/memories.txt"), "w") as file:
        file.write(memories)
        file.close()
    with open(Path("./Server/data/memories/behaviours.txt"), "w") as file:
        file.write(behaviours)
        file.close()


def periodicMemory(existing_memories, previous_behaviours, conversations):
    import os

    import openai

    prompt = f"""You are AVA's dreaming module. You analyze conversations to detect patterns about the USER (not about AVA or conversation dynamics).

        MEMORY FORMAT (TSV - Tab Separated):
        [THINGS I REMEMBER]
        memory text here	fact	0.85

        Fields: text | type | confidence (0.0-1.0)
        Types: fact, preference, style, habit

        ---

        IGNORE THESE:
        - How user addresses AVA (sir, buddy, etc)
        - How AVA responds or behaves
        - Greetings, goodbyes, polite exchanges
        - Meta-conversation about AVA
        - User's name (unless stated as identity)

        FOCUS ON USER'S LIFE:
        - Their preferences, hobbies, interests
        - Their relationships (friends, family mentioned)
        - Their habits, daily patterns
        - Their opinions on topics
        - Their work, school, life situation

        ---

        YOUR TASK:
        Analyze conversations and produce COMPLETE updated lists of memories and behaviours.

        ANALYSIS STEPS:
        1. Start with EXISTING MEMORIES and BEHAVIOURS
        2. Compare with NEW CONVERSATIONS
        3. Decide what to:
        - KEEP: memories still valid
        - REMOVE: memories no longer worth keeping
        - UPDATE: memories that changed
        - CREATE: new memories from strong patterns
        4. Add NEW BEHAVIOURS for weak patterns forming

        RULES:
        - Repeated requests → preference memory
        - Multiple behaviour confirmations → upgrade to memory
        - Explicit statements → high confidence memory
        - Single mentions → behaviour, not memory
        - Old behaviours that never confirmed → remove
        - Memories that contradict new data → remove or update

        ---

        OUTPUT FORMAT - ALWAYS OUTPUT COMPLETE LISTS:

        memories:
        [THINGS I REMEMBER]
        memory text	fact	0.85
        another memory	preference	0.70
        (no memories? output just: (none))

        behaviours:
        - pattern still forming (0.3-0.6)
        - another observation
        (no behaviours? output just: (none))

        ---

        CONTEXT:

        ## PREVIOUS BEHAVIOURS
        {previous_behaviours}

        ## RECENT CONVERSATIONS
        {conversations}

        ## EXISTING MEMORIES
        {existing_memories}

        ---

        OUTPUT COMPLETE UPDATED LISTS NOW:


                """

    model = os.getenv("MODEL_NAME")
    client = openai.OpenAI(
        base_url=os.getenv("OPENAI_BASE_URL"), api_key=os.getenv("OPENAI_API_KEY")
    )
    response = client.responses.create(model=model, input=prompt).output_text
    parts = response.split("behaviours:")

    memories = parts[0].replace("memories:", "").strip()
    behaviours = parts[1].strip()

    write_memories_and_behaviours(memories, behaviours)


dreamerEvent = threading.Event()
dreamerEvent.clear()


def DreamerStart():

    while True:
        ping = dreamerEvent.wait(3600)
        if ping:
            dreamerEvent.clear()
            continue
        else:
            existing_memories, previous_behaviours = get_memories_and_behaviours()
            conversations = str(process_new_memories())
            print("Managing Memories")
            periodicMemory(existing_memories, previous_behaviours, conversations)
