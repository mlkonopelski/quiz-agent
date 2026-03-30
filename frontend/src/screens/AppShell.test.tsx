import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AppShell } from "./AppShell";

interface MockResponseShape {
  status: number;
  json?: unknown;
  text?: string;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetchSequence(sequence: MockResponseShape[]) {
  const queue = [...sequence];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const next = queue.shift();
    if (!next) {
      throw new Error(`Unexpected fetch call for ${String(input)}`);
    }
    if (next.json !== undefined) {
      return jsonResponse(next.json, next.status);
    }
    return new Response(next.text ?? "", { status: next.status });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("AppShell", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    window.localStorage.clear();
  });

  test("shows login when the browser has no active authenticated session", async () => {
    mockFetchSequence([{ status: 401, json: { detail: "Authentication required." } }]);

    render(<AppShell />);

    expect(
      await screen.findByRole("heading", { name: /protected demo login/i }),
    ).toBeVisible();
  });

  test("login success opens the setup card", async () => {
    const user = userEvent.setup();
    mockFetchSequence([
      { status: 401, json: { detail: "Authentication required." } },
      { status: 200, json: { email: "demo@example.com" } },
    ]);

    render(<AppShell />);

    await user.type(screen.getByPlaceholderText("you@example.com"), "demo@example.com");
    await user.type(screen.getByPlaceholderText("Shared demo password"), "secret");
    await user.click(screen.getByRole("button", { name: /enter workspace/i }));

    expect(
      await screen.findByRole("heading", {
        name: /start a new source-driven quiz/i,
      }),
    ).toBeVisible();
  });

  test("clarification replies use the prompt id and the composer stays enabled", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("quiz-agent:last-email", "demo@example.com");
    window.localStorage.setItem(
      "quiz-agent:workflow:demo@example.com",
      "wf-123",
    );

    const fetchMock = mockFetchSequence([
      { status: 200, json: { email: "demo@example.com" } },
      {
        status: 200,
        json: {
          state: "CLARIFYING",
          message: "Need more detail.",
          pending_prompt: {
            prompt_id: "prompt-1",
            text: "What difficulty do you want?",
            turn_no: 1,
          },
          current_question: null,
          result: null,
          review_sessions: null,
          completed_review: null,
          available_actions: ["REPLY_CLARIFICATION", "QUIT"],
          last_error: null,
        },
      },
      { status: 200, json: { status: "sent" } },
      {
        status: 200,
        json: {
          state: "QUIZ_ACTIVE",
          message: "Question 1 of 6",
          pending_prompt: null,
          current_question: {
            question_id: "q-1",
            question_text: "Which layer owns orchestration?",
            options: ["Activities", "Workflow", "DB", "Proxy"],
            is_multi_answer: false,
            position: 1,
            total_questions: 6,
          },
          result: null,
          review_sessions: null,
          completed_review: null,
          available_actions: ["ANSWER_QUESTION", "QUIT"],
          last_error: null,
        },
      },
    ]);

    render(<AppShell />);

    const composer = await screen.findByLabelText(/message input/i);
    expect(composer).toBeEnabled();
    await user.type(composer, "Intermediate");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/sessions/wf-123/commands",
        expect.objectContaining({
          body: expect.stringContaining('"correlation_id":"prompt-1"'),
        }),
      );
    });
  });

  test("continues polling until a clarification prompt is available", async () => {
    window.localStorage.setItem("quiz-agent:last-email", "demo@example.com");
    window.localStorage.setItem(
      "quiz-agent:workflow:demo@example.com",
      "wf-clarify",
    );

    mockFetchSequence([
      { status: 200, json: { email: "demo@example.com" } },
      {
        status: 200,
        json: {
          state: "CLARIFYING",
          message: "Let me understand your preferences...",
          pending_prompt: null,
          current_question: null,
          result: null,
          review_sessions: null,
          completed_review: null,
          available_actions: ["QUIT"],
          last_error: null,
        },
      },
      {
        status: 200,
        json: {
          state: "CLARIFYING",
          message: "What difficulty do you want?",
          pending_prompt: {
            prompt_id: "prompt-2",
            text: "What difficulty do you want?",
            turn_no: 1,
          },
          current_question: null,
          result: null,
          review_sessions: null,
          completed_review: null,
          available_actions: ["REPLY_CLARIFICATION", "QUIT"],
          last_error: null,
        },
      },
      {
        status: 200,
        json: {
          state: "CLARIFYING",
          message: "What difficulty do you want?",
          pending_prompt: {
            prompt_id: "prompt-2",
            text: "What difficulty do you want?",
            turn_no: 1,
          },
          current_question: null,
          result: null,
          review_sessions: null,
          completed_review: null,
          available_actions: ["REPLY_CLARIFICATION", "QUIT"],
          last_error: null,
        },
      },
    ]);

    render(<AppShell />);

    expect(
      await screen.findByText(/let me understand your preferences/i),
    ).toBeVisible();
    expect(await screen.findByLabelText(/message input/i)).toBeDisabled();

    await waitFor(
      () => {
        expect(
          screen.getAllByText("What difficulty do you want?").length,
        ).toBeGreaterThan(0);
        expect(screen.getByLabelText(/message input/i)).toBeEnabled();
      },
      { timeout: 2500 },
    );
  });

  test("quiz answers submit the current question id and disable the composer", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("quiz-agent:last-email", "demo@example.com");
    window.localStorage.setItem(
      "quiz-agent:workflow:demo@example.com",
      "wf-123",
    );

    const fetchMock = mockFetchSequence([
      { status: 200, json: { email: "demo@example.com" } },
      {
        status: 200,
        json: {
          state: "QUIZ_ACTIVE",
          message: "Question 2 of 6",
          pending_prompt: null,
          current_question: {
            question_id: "q-2",
            question_text: "Which worker runs the workflow code?",
            options: ["quiz-workflows", "quiz-db-activities", "quiz-http-activities", "quiz-llm-activities"],
            is_multi_answer: false,
            position: 2,
            total_questions: 6,
          },
          result: null,
          review_sessions: null,
          completed_review: null,
          available_actions: ["ANSWER_QUESTION", "QUIT"],
          last_error: null,
        },
      },
      { status: 200, json: { status: "sent" } },
      {
        status: 200,
        json: {
          state: "RESULT_MENU",
          message: "Quiz complete!",
          pending_prompt: null,
          current_question: null,
          result: {
            final_score: 3.5,
            final_score_pct: 87.5,
            answered_count: 6,
            total_questions: 6,
          },
          review_sessions: null,
          completed_review: null,
          available_actions: ["NEW_QUIZ", "QUIT"],
          last_error: null,
        },
      },
    ]);

    render(<AppShell />);

    expect(await screen.findByLabelText(/message input/i)).toBeDisabled();
    await user.click(screen.getByText("quiz-workflows"));
    await user.click(screen.getByRole("button", { name: /submit answer/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/sessions/wf-123/commands",
        expect.objectContaining({
          body: expect.stringContaining('"correlation_id":"q-2"'),
        }),
      );
    });
  });
});
