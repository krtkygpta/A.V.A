#!/usr/bin/env python3

import json
import os
import sys

import requests
from openai import OpenAI

# ─── CONFIG ─────────────────────────────────────────────
PEAR_HOST = "http://localhost:9863"
MODEL = "openai/gpt-oss-20b"
CLIENT_ID = "music-llm-client"

client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"), base_url="https://api.groq.com/openai/v1"
)

ACCESS_TOKEN = None


# ─── AUTH ───────────────────────────────────────────────


def authenticate():
    global ACCESS_TOKEN
    r = requests.post(f"{PEAR_HOST}/auth/{CLIENT_ID}", timeout=30)

    if r.status_code == 200:
        ACCESS_TOKEN = r.json()["accessToken"]
    else:
        print("Auth failed")
        sys.exit(1)


def headers():
    return {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def api(method, path, body=None):
    r = requests.request(method, f"{PEAR_HOST}{path}", headers=headers(), json=body)
    if r.status_code == 204:
        return {"ok": True}
    return r.json() if r.content else {"ok": True}


# ─── SEARCH PARSER ──────────────────────────────────────


def parse_search(res):
    try:
        results = []

        tabs = (
            res.get("contents", {})
            .get("tabbedSearchResultsRenderer", {})
            .get("tabs", [])
        )

        if not tabs:
            return []

        sections = (
            tabs[0]
            .get("tabRenderer", {})
            .get("content", {})
            .get("sectionListRenderer", {})
            .get("contents", [])
        )

        # 🔁 did you mean
        for section in sections:
            items = section.get("itemSectionRenderer", {}).get("contents", [])
            for item in items:
                if "didYouMeanRenderer" in item:
                    runs = item["didYouMeanRenderer"]["correctedQuery"]["runs"]
                    corrected = "".join([r["text"] for r in runs])
                    return parse_search(
                        api("POST", "/api/v1/search", {"query": corrected})
                    )

        # 🥇 top result
        for section in sections:
            if "musicCardShelfRenderer" in section:
                card = section["musicCardShelfRenderer"]
                try:
                    vid = card["title"]["runs"][0]["navigationEndpoint"][
                        "watchEndpoint"
                    ]["videoId"]
                    title = card["title"]["runs"][0]["text"]
                    return [{"title": title, "videoId": vid}]
                except:
                    pass

        # 📜 fallback list
        for section in sections:
            items = section.get("itemSectionRenderer", {}).get("contents", [])
            for item in items:
                if "musicResponsiveListItemRenderer" in item:
                    r = item["musicResponsiveListItemRenderer"]
                    try:
                        title = r["flexColumns"][0][
                            "musicResponsiveListItemFlexColumnRenderer"
                        ]["text"]["runs"][0]["text"]
                        vid = r["overlay"]["musicItemThumbnailOverlayRenderer"][
                            "content"
                        ]["musicPlayButtonRenderer"]["playNavigationEndpoint"][
                            "watchEndpoint"
                        ]["videoId"]
                        results.append({"title": title, "videoId": vid})
                    except:
                        continue

        return results[:5]

    except:
        return []


# ─── QUEUE PARSER ───────────────────────────────────────


def parse_queue(res):
    items = res.get("items", [])
    cleaned = []

    for item in items:
        video = item.get("playlistPanelVideoRenderer")
        if not video:
            continue

        title = video.get("title", {}).get("runs", [{}])[0].get("text")
        videoId = video.get("videoId")

        artist = video.get("shortBylineText", {}).get("runs", [{}])[0].get("text")

        if not artist:
            runs = video.get("longBylineText", {}).get("runs", [])
            artist = next(
                (r["text"] for r in runs if r.get("text") and r["text"] != " • "), None
            )

        duration = video.get("lengthText", {}).get("runs", [{}])[0].get("text")

        is_current = video.get("selected", False)

        cleaned.append(
            {
                "title": title,
                "artist": artist,
                "videoId": videoId,
                "duration": duration,
                "current": is_current,
            }
        )

    current_index = next((i for i, x in enumerate(cleaned) if x["current"]), 0)

    for i, item in enumerate(cleaned):
        item["index"] = i - current_index

    return cleaned


# ─── TOOL HANDLER ───────────────────────────────────────


def handle_tool(name, args, user_input):
    try:
        if name == "search":
            raw = api("POST", "/api/v1/search", {"query": args["query"]})
            return json.dumps({"songs": parse_search(raw)})

        if name == "add_to_queue":
            body = {"videoId": args["videoId"]}

            if "next" in user_input.lower() or "play" in user_input.lower():
                body["insertPosition"] = "INSERT_AFTER_CURRENT_VIDEO"
            else:
                body["insertPosition"] = args.get("insertPosition", "INSERT_AT_END")

            return json.dumps(api("POST", "/api/v1/queue", body))

        if name == "get_queue":
            raw = api("GET", "/api/v1/queue")
            q = parse_queue(raw)
            limit = args.get("limit", 10)
            return json.dumps({"queue": q[:limit], "total": len(q)})

        if name == "play":
            return json.dumps(api("POST", "/api/v1/play"))

        if name == "pause":
            return json.dumps(api("POST", "/api/v1/pause"))

        if name == "next":
            return json.dumps(api("POST", "/api/v1/next"))

        if name == "previous":
            return json.dumps(api("POST", "/api/v1/previous"))

        if name == "get_song":
            return json.dumps(api("GET", "/api/v1/song"))

        if name == "set_volume":
            return json.dumps(api("POST", "/api/v1/volume", {"volume": args["volume"]}))

        return json.dumps({"error": "unknown tool"})

    except Exception as e:
        return json.dumps({"error": str(e)})


# ─── TOOLS ──────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_queue",
            "parameters": {
                "type": "object",
                "properties": {"videoId": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_queue",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "number"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pause",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "next",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "previous",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_song",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "parameters": {
                "type": "object",
                "properties": {"volume": {"type": "number"}},
                "required": ["volume"],
            },
        },
    },
]


# ─── SYSTEM PROMPT ──────────────────────────────────────

SYSTEM_PROMPT = """
You are a music controller.

RULES:
- NEVER guess videoId
- Always use tools

PLAY RULE:
- "play <song>" = search → add_to_queue (INSERT_AFTER_CURRENT_VIDEO) → next

QUEUE FORMAT:
- index 0 = current song
- index < 0 = previous songs
- index > 0 = upcoming songs

SEARCH:
- Always pick a result and continue
"""


# ─── CHAT LOOP ──────────────────────────────────────────


def chat(user_input, history):
    history.append({"role": "user", "content": user_input})

    last_action = None
    last_result = None

    while True:
        res = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
            tools=TOOLS,
            tool_choice="auto",
        )

        msg = res.choices[0].message

        if not msg.tool_calls:
            history.append({"role": "assistant", "content": msg.content})

            return json.dumps(
                {
                    "status": "success",
                    "message": msg.content or "done",
                    "action": last_action,
                    "data": last_result,
                }
            )

        history.append(msg)

        for tc in msg.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments or "{}")

            last_action = name

            result = handle_tool(name, args, user_input)

            try:
                last_result = json.loads(result)
            except Exception:
                last_result = result

            history.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            if name == "search":
                history.append(
                    {"role": "system", "content": "Pick a song and call add_to_queue"}
                )

            if name == "add_to_queue":
                history.append(
                    {
                        "role": "system",
                        "content": "If user wanted to play it, call next",
                    }
                )


# ─── MAIN ───────────────────────────────────────────────


def main():
    authenticate()

    history = []

    while True:
        user_input = input("You: ")

        if user_input.lower() in ["exit", "quit"]:
            break

        chat(user_input, history)


# ─── FRIDAY ENTRYPOINT ─────────────────────────────────


def run_music_controller(query: str) -> str:
    response = chat(query, [])

    if isinstance(response, str):
        return response

    return json.dumps(response)


# ─── ENTRY ─────────────────────────────────────────────

if __name__ == "__main__":
    main()
else:
    authenticate()
