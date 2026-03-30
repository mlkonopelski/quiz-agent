import { describe, expect, test, vi } from "vitest";

import type { PromptView, QuestionView, WorkflowSnapshot } from "../api/types";
import {
  buildAnswerCommand,
  buildClarificationCommand,
  composerDisabledReason,
  isComposerEnabled,
  shouldBackgroundPoll,
} from "./ui";

describe("ui helpers", () => {
  test("composer is enabled only during clarification", () => {
    const clarifyingSnapshot: WorkflowSnapshot = {
      state: "CLARIFYING",
      message: "Need more detail.",
      pending_prompt: {
        prompt_id: "prompt-1",
        text: "What difficulty should I target?",
        turn_no: 1,
      },
      current_question: null,
      result: null,
      review_sessions: null,
      completed_review: null,
      available_actions: ["REPLY_CLARIFICATION", "QUIT"],
      last_error: null,
    };

    expect(isComposerEnabled(clarifyingSnapshot)).toBe(true);
    expect(
      isComposerEnabled({
        ...clarifyingSnapshot,
        state: "QUIZ_ACTIVE",
        pending_prompt: null,
      }),
    ).toBe(false);
  });

  test("background polling keeps running while clarification is still waiting on a prompt", () => {
    expect(
      shouldBackgroundPoll({
        state: "PREPARING_SOURCE",
      } as WorkflowSnapshot),
    ).toBe(true);
    expect(
      shouldBackgroundPoll({
        state: "GENERATING_QUIZ",
      } as WorkflowSnapshot),
    ).toBe(true);
    expect(
      shouldBackgroundPoll({
        state: "CLARIFYING",
        pending_prompt: null,
      } as WorkflowSnapshot),
    ).toBe(true);
    expect(
      shouldBackgroundPoll({
        state: "CLARIFYING",
        pending_prompt: {
          prompt_id: "prompt-1",
          text: "What level?",
          turn_no: 1,
        },
      } as WorkflowSnapshot),
    ).toBe(false);
  });

  test("clarification commands use the prompt correlation id", () => {
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(
      "00000000-0000-0000-0000-000000000001",
    );
    const prompt: PromptView = {
      prompt_id: "prompt-9",
      text: "What level?",
      turn_no: 1,
    };

    expect(buildClarificationCommand(prompt, "Intermediate")).toEqual({
      command_id: "00000000-0000-0000-0000-000000000001",
      kind: "REPLY_CLARIFICATION",
      correlation_id: "prompt-9",
      text: "Intermediate",
      selected_answers: [],
    });
  });

  test("answer commands use the question correlation id", () => {
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(
      "00000000-0000-0000-0000-000000000002",
    );
    const question: QuestionView = {
      question_id: "question-7",
      question_text: "Pick one",
      options: ["A", "B", "C", "D"],
      is_multi_answer: false,
      position: 1,
      total_questions: 6,
    };

    expect(buildAnswerCommand(question, [2])).toEqual({
      command_id: "00000000-0000-0000-0000-000000000002",
      kind: "ANSWER_QUESTION",
      correlation_id: "question-7",
      selected_answers: [2],
    });
  });

  test("composer helper text changes with the active card type", () => {
    expect(composerDisabledReason(null)).toContain("setup card");
    expect(
      composerDisabledReason({
        state: "QUIZ_ACTIVE",
      } as WorkflowSnapshot),
    ).toContain("quiz card");
  });
});
