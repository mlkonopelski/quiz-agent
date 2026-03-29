Task Description:
Implement in Python an agent that auto-generates a short test on a customizable topic. The agent should then run the quiz, collect the user's answers, and calculate the final score.

The quiz knowledge should be fetched from a Markdown file, provided to the application via configurable URL (e.g. https://github.com/pipecat-ai/pipecat/blob/main/README.md).

The test should consist of 5 to 8 questions, each with a closed list of 4 possible answers.

The agent should score each user’s answer with following rules:

- 4 points - correct answer

- 0 points - wrong answer

- between 0 and 4 - number of correctly selected answers in case of multiple answers question

The final score is calculated as a weighted average of all individual scores. The weight for each answer is a value in a geometric sequence starting from 1.0, with each subsequent weight increased by 10%.

The agent should store all answers and the final score in a database (choose any you prefer).

You may use any library or framework you want, but the preferred ones are OpenAI SDK or Langchain. You can use any free-tier LLM (e.g., Groq).

You can implement user interaction using a command-line interface or any UI, such as Gradio.
