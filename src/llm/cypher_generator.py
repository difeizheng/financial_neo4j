"""
cypher_generator.py

Converts natural language questions to Cypher queries using an LLM,
executes them against Neo4j, then uses the LLM to interpret results.

Supports both OpenAI-compatible APIs and Anthropic Claude.
"""

import json
import logging
from neo4j import GraphDatabase

from src.llm.prompts import (
    SYSTEM_PROMPT,
    CYPHER_GENERATION_PROMPT,
    RESULT_INTERPRETATION_PROMPT,
)

logger = logging.getLogger(__name__)


def _make_llm_client(provider: str, api_key: str, base_url: str, model: str):
    """Return a callable: messages -> str (LLM response text)."""
    if provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        def call(messages: list[dict], system: str = "") -> str:
            resp = client.messages.create(
                model=model,
                max_tokens=2048,
                system=system,
                messages=messages,
            )
            return resp.content[0].text

        return call
    else:
        # OpenAI-compatible (OpenAI, DeepSeek, Qwen, etc.)
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)

        def call(messages: list[dict], system: str = "") -> str:
            if system:
                messages = [{"role": "system", "content": system}] + messages
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=2048,
            )
            return resp.choices[0].message.content

        return call


class FinancialGraphChat:
    def __init__(
        self,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        llm_provider: str,
        llm_api_key: str,
        llm_base_url: str,
        llm_model: str,
    ):
        self.driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
        self.llm = _make_llm_client(llm_provider, llm_api_key, llm_base_url, llm_model)
        self.history: list[dict] = []

    def close(self):
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _run_cypher(self, cypher: str) -> list[dict]:
        """Execute a Cypher query and return results as list of dicts."""
        with self.driver.session() as session:
            result = session.run(cypher)
            return [dict(r) for r in result]

    def _generate_cypher(self, question: str) -> str:
        """Ask LLM to generate a Cypher query for the question."""
        prompt = CYPHER_GENERATION_PROMPT.format(question=question)
        response = self.llm(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
        )
        # Strip any accidental markdown fences
        cypher = response.strip()
        if cypher.startswith("```"):
            lines = cypher.split("\n")
            cypher = "\n".join(
                line for line in lines
                if not line.startswith("```")
            ).strip()
        return cypher

    def _interpret_results(self, question: str, results: list[dict]) -> str:
        """Ask LLM to interpret the query results in natural language."""
        results_str = json.dumps(results, ensure_ascii=False, indent=2)
        if len(results_str) > 4000:
            results_str = results_str[:4000] + "\n... (truncated)"

        prompt = RESULT_INTERPRETATION_PROMPT.format(
            question=question,
            results=results_str,
        )
        # Include conversation history for context
        messages = self.history + [{"role": "user", "content": prompt}]
        return self.llm(messages, system=SYSTEM_PROMPT)

    def ask(self, question: str) -> dict:
        """
        Process a user question end-to-end.
        Returns: {question, cypher, results, answer, error}
        """
        logger.info(f"Question: {question}")

        # Step 1: Generate Cypher
        try:
            cypher = self._generate_cypher(question)
            logger.info(f"Generated Cypher:\n{cypher}")
        except Exception as e:
            return {"question": question, "error": f"Cypher generation failed: {e}"}

        # Step 2: Execute Cypher
        try:
            results = self._run_cypher(cypher)
            logger.info(f"Query returned {len(results)} rows")
        except Exception as e:
            logger.warning(f"Cypher execution failed: {e}")
            # Try to recover with a simpler fallback
            results = []
            cypher = f"-- Failed: {e}\n{cypher}"

        # Step 3: Interpret results
        try:
            answer = self._interpret_results(question, results)
        except Exception as e:
            answer = f"结果解释失败: {e}"

        # Update conversation history
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": answer})
        # Keep history bounded
        if len(self.history) > 20:
            self.history = self.history[-20:]

        return {
            "question": question,
            "cypher": cypher,
            "results": results,
            "answer": answer,
        }
