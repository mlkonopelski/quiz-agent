import type { CommandEnvelope, PromptView, QuestionView, WorkflowSnapshot } from "../api/types";

const POLLABLE_STATES = new Set(["PREPARING_SOURCE", "GENERATING_QUIZ"]);

export function composerDisabledReason(snapshot: WorkflowSnapshot | null): string {
  if (!snapshot) {
    return "Start from the setup card above.";
  }
  if (snapshot.state === "CLARIFYING") {
    return "Reply to the prompt above.";
  }
  if (snapshot.state === "QUIZ_ACTIVE") {
    return "Answer using the quiz card above.";
  }
  if (snapshot.state === "RESULT_MENU") {
    return "Choose an action from the result card above.";
  }
  if (snapshot.state === "REVIEW_LIST") {
    return "Choose a completed quiz from the review card above.";
  }
  if (snapshot.state === "REVIEW_COMPLETED") {
    return "Review mode is read-only. Use the actions on the review card.";
  }
  if (snapshot.state === "PREPARATION_FAILED" || snapshot.state === "GENERATION_FAILED" || snapshot.state === "ABANDONED") {
    return "Use the recovery actions above.";
  }
  if (snapshot.state === "PREPARING_SOURCE" || snapshot.state === "GENERATING_QUIZ") {
    return "The agent is working. New input is temporarily disabled.";
  }
  return "Use the active card above.";
}

export function isComposerEnabled(snapshot: WorkflowSnapshot | null): boolean {
  return snapshot?.state === "CLARIFYING" && snapshot.pending_prompt !== null;
}

export function shouldBackgroundPoll(snapshot: WorkflowSnapshot | null): boolean {
  if (snapshot === null) {
    return false;
  }
  if (POLLABLE_STATES.has(snapshot.state)) {
    return true;
  }
  return snapshot.state === "CLARIFYING" && snapshot.pending_prompt === null;
}

export function snapshotSignature(snapshot: WorkflowSnapshot | null): string {
  if (!snapshot) {
    return "none";
  }
  return JSON.stringify(snapshot);
}

export function buildClarificationCommand(
  prompt: PromptView,
  text: string,
): CommandEnvelope {
  return {
    command_id: crypto.randomUUID(),
    kind: "REPLY_CLARIFICATION",
    correlation_id: prompt.prompt_id,
    text,
    selected_answers: [],
  };
}

export function buildAnswerCommand(
  question: QuestionView,
  selectedAnswers: number[],
): CommandEnvelope {
  return {
    command_id: crypto.randomUUID(),
    kind: "ANSWER_QUESTION",
    correlation_id: question.question_id,
    selected_answers: selectedAnswers,
  };
}
