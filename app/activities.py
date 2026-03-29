from dataclasses import dataclass
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from temporalio import activity
import os
from langchain_core.prompts import ChatPromptTemplate

load_dotenv()

assert os.getenv('OPENAI_API_KEY')

@dataclass
class QuizParams:
    topic: str


@activity.defn
async def generate_quiz(params: QuizParams) -> str:
    template = """You are a quiz generator. Generate 5 multiple-choice quiz questions about Python.
    Focus on the following topic: {topic}
    For each question, provide 4 options (A, B, C, D) and indicate the correct answer."""
    chat_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", template),
            ("human", "Generate the quiz."),
        ]
    )
    chain = chat_prompt | ChatOpenAI()
    result = await chain.ainvoke({"topic": params.topic})
    return str(result.content)