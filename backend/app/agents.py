from langchain.prompts import ChatPromptTemplate
from langchain.tools import Tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.memory import ConversationBufferMemory
from typing import Dict, Any, List, Optional, Tuple
import json
import logging
import aiohttp

logger = logging.getLogger(__name__)

# Decision Agent Prompt
DECISION_PROMPT = """You are a decision-making agent in a UAV flight-log assistant. Your job is to evaluate the response from the Primary LLM and decide if further action is needed.

You have access to the following tools:

{tools}

Tool names: {tool_names}

You will be given:
1. The original user question
2. The response from the Primary LLM

Your task is to output a routing decision in the following format:
```
route: <embedding | tool:retrieve_snippets | tool:detect_anomalies | chat>
reason: <brief justification>
```

Decision Guidelines:
1. Use `embedding` if:
   - The answer is confident and complete
   - All information comes from embedded data
   - No anomalies are mentioned or questions dont need deep analysis

2. Use `tool:retrieve_snippets` if:
   - The answer is vague or hesitant
   - Specific timestamps or events are requested
   - More detailed data is needed

3. Use `tool:detect_anomalies` if:
   - The answer mentions potential issues
   - The question is about problems or errors
   - Anomalies are suspected but not confirmed

IMPORTANT: Your response must be in the exact format shown above, with the route and reason on separate lines.

User question: {question}
Primary LLM response: {primary_response}

{agent_scratchpad}"""

SYSTEM_PROMPT = """You are FlightDataAgent, an AI assistant for UAV flight-log analysis. Your behavior depends on whether a flight log is loaded:

WITHOUT FLIGHT LOG:
- Chat normally about general topics and answer questions about platform and general UAV topics
- You can search the official ArduPilot Plane log message documentation (https://ardupilot.org/plane/docs/logmessages.html) to help answer questions about log messages or UAV telemetry fields.
- If asked about flight data, explain that you need a flight log to be loaded first
- Be helpful and friendly, but clear about your limitations

WITH FLIGHT LOG (when fileKey is provided (which means user uploaded a flight log)):
- You have direct access to the flight data through embeddings
- For basic questions (altitude, duration, errors), answer directly from your embedded knowledge
- Only use tools when:
  1. You need to find specific timestamps or events
  2. You need to investigate anomalies
  3. The question requires detailed analysis of the data
- When using tools:
  1. Don't show the raw tool output to the user, say you are using the tool to do a deeper analysis.
  2. Follow up with your analysis of the output
  3. Keep the conversation flowing naturally

Remember: Be confident in answering from your embedded knowledge. Use tools only when you need to dig deeper.

Current fileKey: {fileKey}"""

async def search_ardupilot_docs(query: str) -> dict:
    """Search the ArduPilot Plane log message documentation for relevant info."""
    # For now, use web_search tool (could be replaced with a custom scraper or offline index)
    from . import functions
    results = await functions.web_search(
        search_term=f"site:https://ardupilot.org/plane/docs/logmessages.html {query}",
        explanation="Search ArduPilot Plane log message documentation for relevant info."
    )
    return results

class ResponseEvaluator:
    """Evaluates LLM responses and decides on tool usage."""
    def __init__(self, llm: ChatGoogleGenerativeAI):
        self.llm = llm
        self.prompt = ChatPromptTemplate.from_template(DECISION_PROMPT)
    
    async def evaluate(self, question: str, primary_response: str, tools: List[Tool]) -> Tuple[str, str]:
        try:
            response = await self.llm.ainvoke(
                self.prompt.format(
                    question=question,
                    primary_response=primary_response,
                    tools="\n".join([f"{tool.name}: {tool.description}" for tool in tools]),
                    tool_names=", ".join([tool.name for tool in tools]),
                    agent_scratchpad=""
                )
            )
            content = response.content.lower()
            if "route:" in content:
                route = content.split("route:")[1].split("\n")[0].strip()
                reason = content.split("reason:")[1].strip() if "reason:" in content else ""
                return route, reason
            else:
                logger.warning("Unexpected response format from evaluator")
                return "embedding", "Defaulting to embedding response"
        except Exception as e:
            logger.error(f"Error in response evaluation: {e}", exc_info=True)
            return "embedding", "Error in evaluation, defaulting to embedding response"

class FlightLogAgents:
    def __init__(self, api_key: str):
        self.decision_llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            temperature=0,
            google_api_key=api_key
        )
        self.tools = [
            Tool(
                name="retrieve_snippets",
                func=self.retrieve_snippets,
                description="Fetch top-k relevant MAVLink snippets for factual questions."
            ),
            Tool(
                name="detect_anomalies",
                func=self.detect_anomalies,
                description="Run server-side checks and return any flagged events."
            )
        ]
        self.memory = ConversationBufferMemory(
            memory_key="chat_history",
            input_key="input",
            output_key="output",
            return_messages=True
        )
        self.evaluator = ResponseEvaluator(self.decision_llm)
    
    def retrieve_snippets(self, fileKey: str, question: str, k: int = 3) -> Dict[str, Any]:
        from .tools import retrieve_snippets
        return retrieve_snippets(fileKey, question, k)
    
    def detect_anomalies(self, fileKey: str) -> Dict[str, Any]:
        from .tools import detect_anomalies
        return detect_anomalies(fileKey)
    
    async def process_question(self, question: str, primary_response: str, fileKey: str = None) -> str:
        """
        Evaluate the primary_response (already generated by the LLM in main.py) and decide whether to return it directly or use tools for deeper analysis.
        """
        try:
            # Step 1: Evaluate response
            route, reason = await self.evaluator.evaluate(
                question=question,
                primary_response=primary_response,
                tools=self.tools
            )
            logger.info(f"Evaluation result - Route: {route}, Reason: {reason}")
            
            # Step 2: Process decision
            if route == "embedding":
                logger.info("Using embedding response directly")
                return primary_response
            elif route.startswith("tool:"):
                tool_name = route.split(":")[1]
                logger.info(f"Executing tool: {tool_name}")
                try:
                    if tool_name == "retrieve_snippets":
                        result = self.retrieve_snippets(fileKey, question)
                        synthesis_prompt = (
                            f"User question: {question}\n"
                            f"Initial analysis: {primary_response}\n"
                            f"(Used the tool 'retrieve_snippets' to do a deeper analysis.)\n"
                            f"Tool output: {json.dumps(result, indent=2)}\n"
                            "Provide a clear, final answer for the user, but do not mention the raw tool output."
                        )
                        # Synthesize final answer using decision_llm
                        synthesis = await self.decision_llm.ainvoke(synthesis_prompt)
                        return f"Used the tool 'retrieve_snippets' to do a deeper analysis.\n\n{synthesis.content}"
                    elif tool_name == "detect_anomalies":
                        anomalies = self.detect_anomalies(fileKey)
                        analysis_prompt = (
                            f"User question: {question}\n"
                            f"Initial analysis: {primary_response}\n"
                            f"(Used the tool 'detect_anomalies' to do a deeper analysis.)\n"
                            f"Tool output: {json.dumps(anomalies, indent=2)}\n"
                            "Provide a clear, final answer for the user, but do not mention the raw tool output."
                        )
                        analysis = await self.decision_llm.ainvoke(analysis_prompt)
                        return f"Used the tool 'detect_anomalies' to do a deeper analysis.\n\n{analysis.content}"
                    else:
                        logger.error(f"Unknown tool requested: {tool_name}")
                        return f"[Error: Unknown tool {tool_name}]"
                except Exception as e:
                    logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
                    return f"[Error executing {tool_name}: {str(e)}]"
            else:  # chat
                logger.info("Using chat response")
                return primary_response
        except Exception as e:
            logger.error(f"Error in multi-agent processing: {e}", exc_info=True)
            return "I apologize, but I'm having trouble processing your request right now. Please try again in a moment."

class FlightLogAgentOrchestrator:
    def __init__(self, api_key: str):
        from langchain_google_genai import ChatGoogleGenerativeAI
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            temperature=0,
            google_api_key=api_key
        )
        self.tools = [
            Tool(
                name="retrieve_snippets",
                func=self.retrieve_snippets,
                description="Fetch top-k relevant MAVLink snippets for factual questions."
            ),
            Tool(
                name="detect_anomalies",
                func=self.detect_anomalies,
                description="Run server-side checks and return any flagged events."
            )
        ]
        self.evaluator = ResponseEvaluator(self.llm)
        self.general_tools = [
            Tool(
                name="search_ardupilot_docs",
                func=lambda query: search_ardupilot_docs(query),
                description="Search the official ArduPilot Plane log message documentation for information about log messages, telemetry fields, or UAV data."
            )
        ]

    def retrieve_snippets(self, fileKey: str, question: str, k: int = 3) -> dict:
        from .tools import retrieve_snippets
        return retrieve_snippets(fileKey, question, k)

    def detect_anomalies(self, fileKey: str) -> dict:
        from .tools import detect_anomalies
        return detect_anomalies(fileKey)

    async def answer_question(self, message: str, fileKey: str = None, chatHistory: list = None, embedding_snippet: str = None) -> str:
        chatHistory = chatHistory or []
        system_prompt = SYSTEM_PROMPT.format(fileKey=fileKey if fileKey else "None")
        history_msgs = [{"role": "system", "content": system_prompt}]
        for turn in chatHistory:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            history_msgs.append({"role": role, "content": content})
        history_msgs.append({"role": "user", "content": message})

        # If we have an embedding snippet, validate and refine it
        if embedding_snippet:
            # First, validate the data
            validation_prompt = (
                f"Raw data from flight log: {embedding_snippet}\n"
                "Validate this data and extract key information:\n"
                "1. What type of message is this? (e.g., GLOBAL_POSITION_INT, VFR_HUD)\n"
                "2. What are the units of measurement?\n"
                "3. What is the timestamp?\n"
                "4. What are the actual values?\n"
                "Format as JSON with keys: message_type, units, timestamp, values"
            )
            validation = await self.llm.ainvoke(validation_prompt)
            
            # Then, refine the response with validated data
            refinement_prompt = (
                f"User question: {message}\n"
                f"Validated data: {validation.content}\n"
                f"Raw data: {embedding_snippet}\n"
                "Provide a clear, concise response that:\n"
                "1. Directly answers the question\n"
                "2. Uses simple, clear language\n"
                "3. Keeps the response under 300 words\n"
                "4. Maintains a helpful, friendly tone\n"
                "5. Focuses on the most relevant information\n\n"
                "IMPORTANT:\n"
                "- NEVER output raw tool code or commands\n"
                "- NEVER mention that you're using tools\n"
                "- NEVER show raw data or technical details\n"
                "- ALWAYS present information in a natural, conversational way\n"
                "- ALWAYS use consistent units (convert to standard units)\n"
                "- ALWAYS validate numbers before presenting them\n"
                "- Format the response as if explaining to a colleague"
            )
            refined = await self.llm.ainvoke(refinement_prompt)
            return refined.content if hasattr(refined, 'content') else str(refined)

        # If no fileKey, just chat and allow ArduPilot doc search tool
        if not fileKey:
            prompt = "".join([f"{msg['role']}: {msg['content']}\n" for msg in history_msgs])
            try:
                response = await self.llm.ainvoke(prompt)
                content = response.content if hasattr(response, 'content') else str(response)
                if "search_ardupilot_docs" in content.lower():
                    import re
                    m = re.search(r"search_ardupilot_docs\((.*?)\)", content, re.IGNORECASE)
                    query = m.group(1) if m else message
                    doc_results = await search_ardupilot_docs(query)
                    synthesis_prompt = (
                        f"User question: {message}\n"
                        f"ArduPilot doc search results: {doc_results}\n"
                        "Provide a clear, concise response that:\n"
                        "1. Directly answers the question\n"
                        "2. Uses simple, clear language\n"
                        "3. Keeps the response under 300 words\n"
                        "4. Maintains a helpful, friendly tone\n"
                        "5. Focuses on the most relevant information\n\n"
                        "IMPORTANT:\n"
                        "- NEVER output raw tool code or commands\n"
                        "- NEVER mention that you're using tools\n"
                        "- NEVER show raw data or technical details\n"
                        "- ALWAYS present information in a natural, conversational way\n"
                        "- Format the response as if explaining to a colleague"
                    )
                    synthesis = await self.llm.ainvoke(synthesis_prompt)
                    return synthesis.content if hasattr(synthesis, 'content') else str(synthesis)
                return content
            except Exception as e:
                logger.error(f"Error in LLM chat (no flight log): {e}", exc_info=True)
                return f"[Error communicating with LLM: {e}]"

        # If fileKey is present, do LLM call and tool routing
        try:
            prompt = "".join([f"{msg['role']}: {msg['content']}\n" for msg in history_msgs])
            response = await self.llm.ainvoke(prompt)
            primary_response = response.content if hasattr(response, 'content') else str(response)
            route, reason = await self.evaluator.evaluate(
                question=message,
                primary_response=primary_response,
                tools=self.tools
            )
            logger.info(f"Evaluation result - Route: {route}, Reason: {reason}")
            
            if route == "embedding":
                # Refine the embedding response with validation
                refinement_prompt = (
                    f"User question: {message}\n"
                    f"Raw response: {primary_response}\n"
                    "Provide a clear, concise response that:\n"
                    "1. Directly answers the question\n"
                    "2. Uses simple, clear language\n"
                    "3. Keeps the response under 300 words\n"
                    "4. Maintains a helpful, friendly tone\n"
                    "5. Focuses on the most relevant information\n\n"
                    "IMPORTANT:\n"
                    "- NEVER output raw tool code or commands\n"
                    "- NEVER mention that you're using tools\n"
                    "- NEVER show raw data or technical details\n"
                    "- ALWAYS present information in a natural, conversational way\n"
                    "- ALWAYS use consistent units (convert to standard units)\n"
                    "- ALWAYS validate numbers before presenting them\n"
                    "- Format the response as if explaining to a colleague"
                )
                refined = await self.llm.ainvoke(refinement_prompt)
                return refined.content if hasattr(refined, 'content') else str(refined)
            
            elif route.startswith("tool:"):
                tool_name = route.split(":")[1]
                try:
                    if tool_name == "retrieve_snippets":
                        result = self.retrieve_snippets(fileKey, message)
                        # First validate the data
                        validation_prompt = (
                            f"Tool output: {json.dumps(result, indent=2)}\n"
                            "Validate this data and extract key information:\n"
                            "1. What type of message is this? (e.g., GLOBAL_POSITION_INT, VFR_HUD)\n"
                            "2. What are the units of measurement?\n"
                            "3. What is the timestamp?\n"
                            "4. What are the actual values?\n"
                            "Format as JSON with keys: message_type, units, timestamp, values"
                        )
                        validation = await self.llm.ainvoke(validation_prompt)
                        
                        synthesis_prompt = (
                            f"User question: {message}\n"
                            f"Validated data: {validation.content}\n"
                            f"Initial analysis: {primary_response}\n"
                            "Provide a clear, concise response that:\n"
                            "1. Directly answers the question\n"
                            "2. Uses simple, clear language\n"
                            "3. Keeps the response under 300 words\n"
                            "4. Maintains a helpful, friendly tone\n"
                            "5. Focuses on the most relevant information\n\n"
                            "IMPORTANT:\n"
                            "- NEVER output raw tool code or commands\n"
                            "- NEVER mention that you're using tools\n"
                            "- NEVER show raw data or technical details\n"
                            "- ALWAYS present information in a natural, conversational way\n"
                            "- ALWAYS use consistent units (convert to standard units)\n"
                            "- ALWAYS validate numbers before presenting them\n"
                            "- Format the response as if explaining to a colleague"
                        )
                        synthesis = await self.llm.ainvoke(synthesis_prompt)
                        return synthesis.content if hasattr(synthesis, 'content') else str(synthesis)
                    
                    elif tool_name == "detect_anomalies":
                        anomalies = self.detect_anomalies(fileKey)
                        # First validate the anomalies
                        validation_prompt = (
                            f"Tool output: {json.dumps(anomalies, indent=2)}\n"
                            "Validate these anomalies and categorize them:\n"
                            "1. What types of anomalies are present?\n"
                            "2. What are the timestamps?\n"
                            "3. What are the severity levels?\n"
                            "4. What are the potential causes?\n"
                            "Format as JSON with keys: anomaly_types, timestamps, severity, causes"
                        )
                        validation = await self.llm.ainvoke(validation_prompt)
                        
                        analysis_prompt = (
                            f"User question: {message}\n"
                            f"Validated anomalies: {validation.content}\n"
                            f"Initial analysis: {primary_response}\n"
                            "Provide a clear, concise response that:\n"
                            "1. Directly answers the question\n"
                            "2. Uses simple, clear language\n"
                            "3. Keeps the response under 300 words\n"
                            "4. Maintains a helpful, friendly tone\n"
                            "5. Focuses on the most relevant information\n\n"
                            "IMPORTANT:\n"
                            "- NEVER output raw tool code or commands\n"
                            "- NEVER mention that you're using tools\n"
                            "- NEVER show raw data or technical details\n"
                            "- ALWAYS present information in a natural, conversational way\n"
                            "- If there are issues, explain them briefly without being overly concerned\n"
                            "- If there are no issues, state that clearly\n"
                            "- Format the response as if explaining to a colleague"
                        )
                        analysis = await self.llm.ainvoke(analysis_prompt)
                        return analysis.content if hasattr(analysis, 'content') else str(analysis)
                    
                    else:
                        logger.error(f"Unknown tool requested: {tool_name}")
                        return f"[Error: Unknown tool {tool_name}]"
                except Exception as e:
                    logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
                    return f"[Error executing {tool_name}: {str(e)}]"
            else:
                # Refine the chat response
                refinement_prompt = (
                    f"User question: {message}\n"
                    f"Raw response: {primary_response}\n"
                    "Provide a clear, concise response that:\n"
                    "1. Directly answers the question\n"
                    "2. Uses simple, clear language\n"
                    "3. Keeps the response under 300 words\n"
                    "4. Maintains a helpful, friendly tone\n"
                    "5. Focuses on the most relevant information\n\n"
                    "IMPORTANT:\n"
                    "- NEVER output raw tool code or commands\n"
                    "- NEVER mention that you're using tools\n"
                    "- NEVER show raw data or technical details\n"
                    "- ALWAYS present information in a natural, conversational way\n"
                    "- Format the response as if explaining to a colleague"
                )
                refined = await self.llm.ainvoke(refinement_prompt)
                return refined.content if hasattr(refined, 'content') else str(refined)
        except Exception as e:
            logger.error(f"Error in agent orchestration: {e}", exc_info=True)
            return "I apologize, but I'm having trouble processing your request right now. Please try again in a moment." 