"""
Deep Research Agent — Groq (via OpenAI compatibility layer) + Tavily
----------------------------------------------------------------------
Phases:
  1. Initial thinking  — decompose the question        (Groq)
  2. Web search        — generate queries + search web  (Tavily)
  3. Grounding         — cross-check and verify sources (Groq)
  4. Deep re-analysis  — rethink with evidence          (Groq)
  5. Final synthesis   — compose the answer             (Groq)

Install:
    pip install openai tavily-python

Keys:
    export GROQ_API_KEY=...
    export TAVILY_API_KEY=...
    (or the script will prompt you)
"""

import os
import json
import textwrap
from openai import OpenAI
from tavily import TavilyClient

# ── Clients ────────────────────────────────────────────────────────────────────

# Groq via OpenAI compatibility layer — just swap base_url
client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
)

tavily_client = TavilyClient(
    api_key=os.getenv("TAVILY_API_KEY")
)

# Model to use for all reasoning phases
# Good options: "llama-3.3-70b-versatile", "deepseek-r1-distill-llama-70b", "mixtral-8x7b-32768"
GROQ_MODEL = "llama-3.3-70b-versatile"

DIVIDER = "─" * 70


# ── Helpers ────────────────────────────────────────────────────────────────────

def print_phase(number, title):
    print(f"\n{DIVIDER}")
    print(f"  Phase {number}: {title}")
    print(DIVIDER)


def print_block(label, text):
    print(f"\n[{label}]\n")
    for line in text.strip().split("\n"):
        print(textwrap.fill(line, width=80) if line.strip() else "")


def think(system_prompt: str, user_prompt: str) -> str:
    """Single-turn reasoning call via Groq (OpenAI-compatible)."""
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        max_tokens=2048,
        temperature=0.6,
    )
    return response.choices[0].message.content.strip()


def search(query: str) -> dict:
    """Search the web via Tavily. Returns {"text": str, "urls": [str]}."""
    response = tavily_client.search(
        query=query,
        search_depth="advanced",
        max_results=5,
        include_answer=True,
        include_raw_content=False,
    )

    parts = []
    urls = []

    if response.get("answer"):
        parts.append(f"Summary: {response['answer']}")

    for r in response.get("results", []):
        title   = r.get("title", "")
        url     = r.get("url", "")
        content = r.get("content", "")
        if url:
            urls.append(url)
        if content:
            parts.append(f"• {title}\n  {content[:400]}")

    return {
        "text": "\n\n".join(parts) or "No results.",
        "urls": urls,
    }


# ── Main agent ─────────────────────────────────────────────────────────────────

def run_research(question: str):
    print(f"\n{'═' * 70}")
    print(f"  Deep Research Agent  |  Groq + Tavily")
    print(f"{'═' * 70}")
    print(f"\n  Question: {question}\n")

    # ── Phase 1: Initial thinking ──────────────────────────────────────────────
    print_phase(1, "Initial Thinking")

    think1 = think(
        system_prompt=(
            "You are a deep research assistant. When given a question, think carefully "
            "about: what are the key sub-questions to answer, what aspects need "
            "investigation, what could be controversial or nuanced, and what search "
            "queries would best ground this. Be thorough in your chain of thought."
        ),
        user_prompt=(
            f"Question: {question}\n\n"
            "Think deeply about this. What are the key dimensions, sub-questions, "
            "and potential angles to research?"
        ),
    )
    print_block("Initial Analysis", think1)

    # ── Phase 2: Web search ────────────────────────────────────────────────────
    print_phase(2, "Web Search")

    query_plan = think(
        system_prompt=(
            "You are a search strategist. Given a research question and initial "
            "analysis, output exactly 3 distinct search queries (one per line, "
            "no numbering, no quotes, no extra text) that would find authoritative "
            "and diverse sources. Keep each query concise and specific."
        ),
        user_prompt=(
            f"Original question: {question}\n\n"
            f"Initial analysis:\n{think1}\n\n"
            "Output 3 search queries, one per line:"
        ),
    )

    queries = [q.strip() for q in query_plan.strip().split("\n") if q.strip()][:3]
    print(f"\n  Generated {len(queries)} search queries:")
    for i, q in enumerate(queries, 1):
        print(f"    {i}. {q}")

    search_results = []
    all_urls = []

    for i, query in enumerate(queries, 1):
        print(f"\n  Searching [{i}/{len(queries)}]: {query}")
        try:
            result = search(query)
            search_results.append({"query": query, "text": result["text"]})
            all_urls.extend(result["urls"])
            preview = result["text"][:200].replace("\n", " ")
            print(f"  → {preview}{'...' if len(result['text']) > 200 else ''}")
        except Exception as e:
            print(f"  ✗ Search failed: {e}")
            search_results.append({"query": query, "text": f"Search failed: {e}"})

    if all_urls:
        print(f"\n  Sources found:")
        seen = set()
        for url in all_urls:
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc.replace("www.", "")
                if domain not in seen:
                    seen.add(domain)
                    print(f"    • {domain}")
            except Exception:
                pass

    # ── Phase 3: Grounding ─────────────────────────────────────────────────────
    print_phase(3, "Grounding & Cross-Checking")

    combined = "\n\n".join(
        f'[Source {i+1}: "{r["query"]}"]\n{r["text"]}'
        for i, r in enumerate(search_results)
    )

    grounding = think(
        system_prompt=(
            "You are a fact-checker and research analyst. Cross-check the provided "
            "search results: identify agreements and contradictions between sources, "
            "note what is well-supported vs uncertain, highlight the most credible "
            "findings, and flag any potential biases or gaps."
        ),
        user_prompt=(
            f"Question: {question}\n\n"
            f"Search results:\n{combined}\n\n"
            "Analyze what the sources agree on, where they conflict, what is "
            "well-supported, and what remains uncertain."
        ),
    )
    print_block("Grounding Analysis", grounding)

    # ── Phase 4: Deep re-analysis ──────────────────────────────────────────────
    print_phase(4, "Deep Re-Analysis")

    think2 = think(
        system_prompt=(
            "You are a deep analytical thinker. You have initial thoughts and now "
            "grounded evidence. Re-examine the question with this new information. "
            "Update your thinking, resolve contradictions, consider what the evidence "
            "actually implies, and build toward a nuanced, well-supported conclusion. "
            "Think at length and be intellectually honest about uncertainty."
        ),
        user_prompt=(
            f"Original question: {question}\n\n"
            f"Initial thinking:\n{think1}\n\n"
            f"Grounded evidence analysis:\n{grounding}\n\n"
            "Now re-analyze deeply. What does the evidence actually tell us? "
            "What should the final answer focus on?"
        ),
    )
    print_block("Re-Analysis", think2)

    # ── Phase 5: Final synthesis ───────────────────────────────────────────────
    print_phase(5, "Final Synthesis")

    final_answer = think(
        system_prompt=(
            "You are a senior research analyst. Synthesize all the thinking and "
            "evidence into a clear, comprehensive, well-structured final answer. "
            "Be direct and informative. Use paragraphs. Note where sources agreed "
            "or disagreed when relevant. Provide a real, substantive answer — "
            "do not hedge unnecessarily."
        ),
        user_prompt=(
            f"Question: {question}\n\n"
            f"Initial analysis:\n{think1}\n\n"
            f"Evidence grounding:\n{grounding}\n\n"
            f"Deep re-analysis:\n{think2}\n\n"
            "Write the final comprehensive answer:"
        ),
    )

    print(f"\n{'═' * 70}")
    print("  FINAL ANSWER")
    print(f"{'═' * 70}\n")
    for line in final_answer.strip().split("\n"):
        print(textwrap.fill(line, width=80) if line.strip() else "")
    print(f"\n{'═' * 70}\n")

    return {
        "question": question,
        "model": GROQ_MODEL,
        "think1": think1,
        "search_results": search_results,
        "sources": all_urls,
        "grounding": grounding,
        "think2": think2,
        "final_answer": final_answer,
    }

def research(query):
    import json
    import requests
    import os
    
    SERVER_URL = os.getenv("AVA_SERVER_URL", "http://127.0.0.1:8765").rstrip("/")
    DEFAULT_TIMEOUT = float(os.getenv("AVA_SERVER_TIMEOUT", "4"))
    
    try:
        response = requests.post(
            f"{SERVER_URL}/tools/research",
            json={"question": query},
            timeout=max(DEFAULT_TIMEOUT, 600.0)
        )
        response.raise_for_status()
        result = response.json()
        
        if result.get("status") == "success":
            return json.dumps(result.get("content"))
        else:
            return json.dumps(f"Error: {result.get('content', 'Unknown error')}")
    except requests.exceptions.RequestException as e:
        return json.dumps(f"Error: Server request failed: {str(e)}")

# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
    else:
        print("\nDeep Research Agent  |  Groq + Tavily")
        print("--------------------------------------")
        question = input("Enter your research question: ").strip()

    if not question:
        print("No question provided. Exiting.")
        sys.exit(1)

    result = run_research(question)

    save = input("\nSave full research trace to JSON? (y/n): ").strip().lower()
    if save == "y":
        filename = "research_output.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Saved to {filename}")