"""Deep research agent using Groq + Tavily."""

import traceback
from typing import Any
from openai import OpenAI
from tavily import TavilyClient


class ResearchAgent:
    MODEL = "llama-3.3-70b-versatile"

    def __init__(self, groq_key: str, tavily_key: str):
        self.groq = OpenAI(api_key=groq_key, base_url="https://api.groq.com/openai/v1")
        self.tavily = TavilyClient(api_key=tavily_key)

    def _think(self, system: str, user: str) -> str:
        r = self.groq.chat.completions.create(
            model=self.MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=2048, temperature=0.6
        )
        return r.choices[0].message.content.strip()

    def _search(self, query: str) -> dict:
        try:
            r = self.tavily.search(query=query, search_depth="advanced", max_results=5, include_answer=True)
            parts = []
            urls = []

            # Handle answer if present
            if r.get("answer"):
                parts.append(f"Summary: {r['answer']}")

            # Handle results
            for x in r.get("results", []):
                if x.get("url"):
                    urls.append(x["url"])
                if x.get("content"):
                    parts.append(f"* {x.get('title', '')}\n  {x['content'][:400]}")

            return {"text": "\n\n".join(parts) or "No results.", "urls": urls}
        except Exception as e:
            print(f"[ResearchAgent] Search error: {e}")
            return {"text": f"Search failed: {e}", "urls": []}

    def execute(self, question: str) -> dict[str, Any]:
        try:
            print(f"[ResearchAgent] Starting research: {question}")

            # Phase 1: Initial thinking
            think1 = self._think(
                "You are a deep research assistant. Think about key sub-questions, angles to research.",
                f"Question: {question}\nWhat are the key dimensions and angles?"
            )
            print(f"[ResearchAgent] Phase 1 complete")

            # Phase 2: Search queries
            qp = self._think(
                "Output exactly 3 search queries, one per line, no numbering.",
                f"Question: {question}\nAnalysis:\n{think1}\n3 queries:"
            )
            queries = [q.strip() for q in qp.split("\n") if q.strip()][:3]
            print(f"[ResearchAgent] Phase 2: {len(queries)} queries")

            results, urls = [], []
            for i, q in enumerate(queries, 1):
                try:
                    sr = self._search(q)
                    results.append({"query": q, "text": sr["text"]})
                    urls.extend(sr["urls"])
                    print(f"[ResearchAgent] Search {i}/{len(queries)} done")
                except Exception as e:
                    results.append({"query": q, "text": f"Failed: {e}"})

            # Phase 3: Grounding
            combined = "\n\n".join(f'[Source {i+1}]\n{r["text"]}' for i, r in enumerate(results))
            grounding = self._think(
                "Cross-check sources for agreements, contradictions, credibility.",
                f"Question: {question}\nSources:\n{combined}\nAnalyze."
            )
            print(f"[ResearchAgent] Phase 3 complete")

            # Phase 4: Re-analysis
            think2 = self._think(
                "Re-examine with evidence. Be intellectually honest about uncertainty.",
                f"Question: {question}\nInitial:\n{think1}\nEvidence:\n{grounding}\nRe-analyze."
            )
            print(f"[ResearchAgent] Phase 4 complete")

            # Phase 5: Final
            final = self._think(
                "Synthesize into a clear, comprehensive answer.",
                f"Question: {question}\nEvidence:\n{grounding}\nAnalysis:\n{think2}\nFinal answer:"
            )
            print(f"[ResearchAgent] Phase 5 complete")

            return {"status": "success", "content": final}
        except Exception as e:
            print(f"[ResearchAgent] Error: {e}\n{traceback.format_exc()}")
            return {"status": "error", "content": str(e)}