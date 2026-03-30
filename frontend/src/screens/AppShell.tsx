import {
  startTransition,
  useEffect,
  useEffectEvent,
  useState,
  type FormEvent,
} from "react";

import { ApiError, UnauthorizedError, apiClient } from "../api/client";
import type { CommandEnvelope, WorkflowSnapshot } from "../api/types";
import {
  clearTranscript,
  clearWorkflowId,
  loadLastEmail,
  loadTranscript,
  loadWorkflowId,
  saveLastEmail,
  saveTranscript,
  saveWorkflowId,
} from "../app/storage";
import {
  appendAnsweredQuestion,
  appendClarificationReply,
  appendResult,
  appendReview,
  ensurePromptRecorded,
  type TranscriptEntry,
} from "../app/transcript";
import {
  buildAnswerCommand,
  buildClarificationCommand,
  composerDisabledReason,
  isComposerEnabled,
  shouldBackgroundPoll,
  snapshotSignature,
} from "../app/ui";
import { ChatLayout } from "../components/ChatLayout";
import {
  AssistantBubble,
  FailureCard,
  LoadingCard,
  QuizCard,
  ResultCard,
  ReviewCard,
  ReviewListCard,
  SetupCard,
  UserBubble,
} from "../components/Cards";

const DEFAULT_TOPIC = "Pipecat";
const DEFAULT_MARKDOWN_URL =
  "https://github.com/pipecat-ai/pipecat/blob/main/README.md";
const FOLLOW_UP_ATTEMPTS = 8;
const FOLLOW_UP_INTERVAL_MS = 500;
const BACKGROUND_POLL_MS = 1000;

type AuthStatus = "checking" | "anonymous" | "authenticated";

export function AppShell() {
  const [authStatus, setAuthStatus] = useState<AuthStatus>("checking");
  const [email, setEmail] = useState<string | null>(null);
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<WorkflowSnapshot | null>(null);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [loginEmail, setLoginEmail] = useState(loadLastEmail());
  const [loginPassword, setLoginPassword] = useState("");
  const [topic, setTopic] = useState(DEFAULT_TOPIC);
  const [markdownUrl, setMarkdownUrl] = useState(DEFAULT_MARKDOWN_URL);
  const [composerText, setComposerText] = useState("");
  const [selectedAnswers, setSelectedAnswers] = useState<number[]>([]);
  const [busyLabel, setBusyLabel] = useState<string | null>(null);
  const [uiError, setUiError] = useState<string | null>(null);

  const busy = busyLabel !== null;

  const persistTranscript = useEffectEvent(
    (targetWorkflowId: string, nextTranscript: TranscriptEntry[]) => {
      saveTranscript(targetWorkflowId, nextTranscript);
      setTranscript(nextTranscript);
    },
  );

  const clearClientWorkflowState = useEffectEvent((targetEmail: string | null) => {
    if (targetEmail) {
      clearWorkflowId(targetEmail);
    }
    if (workflowId) {
      clearTranscript(workflowId);
    }
    setWorkflowId(null);
    setSnapshot(null);
    setTranscript([]);
    setSelectedAnswers([]);
    setComposerText("");
  });

  const handleApiFailure = useEffectEvent((error: unknown) => {
    if (error instanceof UnauthorizedError) {
      clearClientWorkflowState(email);
      setEmail(null);
      setAuthStatus("anonymous");
      setLoginPassword("");
      setUiError("Your session expired. Please sign in again.");
      return true;
    }

    if (error instanceof ApiError) {
      setUiError(error.message);
      return true;
    }

    if (error instanceof Error) {
      setUiError(error.message);
      return true;
    }

    setUiError("The UI hit an unexpected error.");
    return true;
  });

  const applySnapshot = useEffectEvent(
    (targetWorkflowId: string, nextSnapshot: WorkflowSnapshot) => {
      const prev = snapshot;
      if (prev && prev.state !== nextSnapshot.state) {
        if (prev.state === "RESULT_MENU" && prev.result) {
          setTranscript((current) => {
            const next = appendResult(current, prev.result!, `result:${targetWorkflowId}`);
            saveTranscript(targetWorkflowId, next);
            return next;
          });
        }
        if (prev.state === "REVIEW_COMPLETED" && prev.completed_review) {
          setTranscript((current) => {
            const next = appendReview(
              current,
              prev.completed_review!,
              `review:${prev.completed_review!.session_id}`,
            );
            saveTranscript(targetWorkflowId, next);
            return next;
          });
        }
      }

      startTransition(() => {
        setSnapshot(nextSnapshot);
        setSelectedAnswers([]);
      });

      if (nextSnapshot.state === "CLARIFYING" && nextSnapshot.pending_prompt) {
        setTranscript((currentTranscript) => {
          const nextTranscript = ensurePromptRecorded(
            currentTranscript,
            nextSnapshot.pending_prompt!,
          );
          saveTranscript(targetWorkflowId, nextTranscript);
          return nextTranscript;
        });
      }
    },
  );

  const refreshSnapshot = useEffectEvent(async (targetWorkflowId?: string) => {
    const resolvedWorkflowId = targetWorkflowId ?? workflowId;
    if (!resolvedWorkflowId) {
      return;
    }
    try {
      const nextSnapshot = await apiClient.getSnapshot(resolvedWorkflowId);
      setUiError(null);
      applySnapshot(resolvedWorkflowId, nextSnapshot);
    } catch (error) {
      handleApiFailure(error);
    }
  });

  const waitForSnapshotChange = useEffectEvent(
    async (targetWorkflowId: string, previousSignature: string) => {
      for (let attempt = 0; attempt < FOLLOW_UP_ATTEMPTS; attempt += 1) {
        await new Promise((resolve) =>
          window.setTimeout(resolve, FOLLOW_UP_INTERVAL_MS),
        );
        try {
          const nextSnapshot = await apiClient.getSnapshot(targetWorkflowId);
          applySnapshot(targetWorkflowId, nextSnapshot);
          if (snapshotSignature(nextSnapshot) !== previousSignature) {
            break;
          }
        } catch (error) {
          if (handleApiFailure(error)) {
            break;
          }
        }
      }
    },
  );

  const ensureSession = useEffectEvent(async (): Promise<string | null> => {
    if (workflowId) {
      return workflowId;
    }
    if (!email) {
      return null;
    }

    try {
      const nextWorkflowId = await apiClient.createSession();
      setWorkflowId(nextWorkflowId);
      saveWorkflowId(email, nextWorkflowId);
      persistTranscript(nextWorkflowId, []);
      return nextWorkflowId;
    } catch (error) {
      handleApiFailure(error);
      return null;
    }
  });

  useEffect(() => {
    let ignore = false;

    async function bootstrap(): Promise<void> {
      try {
        const currentUser = await apiClient.getCurrentUser();
        if (ignore) {
          return;
        }
        setAuthStatus("authenticated");
        setEmail(currentUser.email);
        setLoginEmail(currentUser.email);
        saveLastEmail(currentUser.email);

        let resolvedWorkflowId = loadWorkflowId(currentUser.email);

        if (!resolvedWorkflowId) {
          resolvedWorkflowId = await apiClient.getActiveSession();
          if (ignore) return;
          if (resolvedWorkflowId) {
            saveWorkflowId(currentUser.email, resolvedWorkflowId);
          }
        }

        if (!resolvedWorkflowId) {
          setWorkflowId(null);
          setSnapshot(null);
          setTranscript([]);
          return;
        }

        setWorkflowId(resolvedWorkflowId);
        setTranscript(loadTranscript(resolvedWorkflowId));
        const nextSnapshot = await apiClient.getSnapshot(resolvedWorkflowId);
        if (ignore) {
          return;
        }
        applySnapshot(resolvedWorkflowId, nextSnapshot);
      } catch (error) {
        if (ignore) {
          return;
        }
        setAuthStatus("anonymous");
        setEmail(null);
        setSnapshot(null);
        setTranscript([]);
        if (!(error instanceof UnauthorizedError)) {
          handleApiFailure(error);
        }
      } finally {
        if (!ignore) {
          setAuthStatus((current) =>
            current === "checking" ? "anonymous" : current,
          );
        }
      }
    }

    void bootstrap();

    return () => {
      ignore = true;
    };
  }, []);

  useEffect(() => {
    if (!workflowId || !shouldBackgroundPoll(snapshot)) {
      return;
    }

    const timerId = window.setInterval(() => {
      void refreshSnapshot(workflowId);
    }, BACKGROUND_POLL_MS);

    return () => {
      window.clearInterval(timerId);
    };
  }, [snapshot, workflowId]);

  async function handleLoginSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedEmail = loginEmail.trim().toLowerCase();
    if (!normalizedEmail || !loginPassword.trim()) {
      setUiError("Email and password are required.");
      return;
    }

    setBusyLabel("Signing in...");
    setUiError(null);

    try {
      const session = await apiClient.login(normalizedEmail, loginPassword);
      saveLastEmail(session.email);
      clearClientWorkflowState(email);
      setEmail(session.email);
      setAuthStatus("authenticated");
      setLoginEmail(session.email);
      setLoginPassword("");

      let resolvedWorkflowId = loadWorkflowId(session.email);

      if (!resolvedWorkflowId) {
        resolvedWorkflowId = await apiClient.getActiveSession();
        if (resolvedWorkflowId) {
          saveWorkflowId(session.email, resolvedWorkflowId);
        }
      }

      if (resolvedWorkflowId) {
        setWorkflowId(resolvedWorkflowId);
        setTranscript(loadTranscript(resolvedWorkflowId));
        const nextSnapshot = await apiClient.getSnapshot(resolvedWorkflowId);
        applySnapshot(resolvedWorkflowId, nextSnapshot);
      } else {
        setWorkflowId(null);
        setSnapshot(null);
        setTranscript([]);
      }
    } catch (error) {
      handleApiFailure(error);
    } finally {
      setBusyLabel(null);
    }
  }

  async function handleLogout() {
    setBusyLabel("Logging out...");
    try {
      await apiClient.logout();
    } catch {
      // Best-effort cleanup; the local session should still be reset.
    } finally {
      clearClientWorkflowState(email);
      setEmail(null);
      setAuthStatus("anonymous");
      setLoginPassword("");
      setBusyLabel(null);
    }
  }

  async function sendWorkflowCommand(
    command: CommandEnvelope,
    options: {
      busyMessage: string;
      clearLocalTranscriptFirst?: boolean;
    },
  ) {
    const activeWorkflowId = await ensureSession();
    if (!activeWorkflowId) {
      return;
    }

    if (options.clearLocalTranscriptFirst) {
      persistTranscript(activeWorkflowId, []);
    }

    setBusyLabel(options.busyMessage);
    setUiError(null);
    const previousSignature = snapshotSignature(snapshot);

    try {
      await apiClient.sendCommand(activeWorkflowId, command);
      if (command.kind === "QUIT") {
        clearClientWorkflowState(email);
        return;
      }
      if (command.kind === "BACK_TO_MENU") {
        // Transcript preserved — frozen cards remain visible as conversation history
      }
      await waitForSnapshotChange(activeWorkflowId, previousSignature);
    } catch (error) {
      handleApiFailure(error);
    } finally {
      setBusyLabel(null);
    }
  }

  async function handleStartQuiz() {
    if (!topic.trim() || !markdownUrl.trim()) {
      setUiError("Topic and markdown URL are required.");
      return;
    }

    await sendWorkflowCommand(
      {
        command_id: crypto.randomUUID(),
        kind: "NEW_QUIZ",
        topic: topic.trim(),
        markdown_url: markdownUrl.trim(),
        selected_answers: [],
      },
      {
        busyMessage: "Starting quiz...",
        clearLocalTranscriptFirst: true,
      },
    );
  }

  async function handleLoadCompletedList() {
    await sendWorkflowCommand(
      {
        command_id: crypto.randomUUID(),
        kind: "LOAD_COMPLETED_QUIZ",
        selected_answers: [],
      },
      { busyMessage: "Loading quizzes..." },
    );
  }

  async function handleRegenerate() {
    await sendWorkflowCommand(
      {
        command_id: crypto.randomUUID(),
        kind: "REGENERATE_LAST_TOPIC",
        selected_answers: [],
      },
      {
        busyMessage: "Regenerating...",
        clearLocalTranscriptFirst: true,
      },
    );
  }

  async function handleBackToMenu() {
    await sendWorkflowCommand(
      {
        command_id: crypto.randomUUID(),
        kind: "BACK_TO_MENU",
        selected_answers: [],
      },
      { busyMessage: "Returning to menu..." },
    );
  }

  async function handleQuit() {
    await sendWorkflowCommand(
      {
        command_id: crypto.randomUUID(),
        kind: "QUIT",
        selected_answers: [],
      },
      { busyMessage: "Closing..." },
    );
  }

  async function handleClarificationSubmit() {
    if (
      !snapshot?.pending_prompt ||
      !isComposerEnabled(snapshot) ||
      !composerText.trim()
    ) {
      return;
    }

    const activeWorkflowId = await ensureSession();
    if (!activeWorkflowId) {
      return;
    }

    const nextTranscript = appendClarificationReply(
      transcript,
      composerText.trim(),
    );
    persistTranscript(activeWorkflowId, nextTranscript);

    const command = buildClarificationCommand(
      snapshot.pending_prompt,
      composerText.trim(),
    );
    setComposerText("");

    await sendWorkflowCommand(command, {
      busyMessage: "Sending reply...",
    });
  }

  function toggleAnswer(answerIndex: number) {
    const question = snapshot?.current_question;
    if (!question) {
      return;
    }

    setSelectedAnswers((current) => {
      if (!question.is_multi_answer) {
        return [answerIndex];
      }
      if (current.includes(answerIndex)) {
        return current.filter((value) => value !== answerIndex);
      }
      return [...current, answerIndex].sort((left, right) => left - right);
    });
  }

  async function handleAnswerSubmit() {
    const question = snapshot?.current_question;
    if (!question) {
      return;
    }
    if (selectedAnswers.length === 0) {
      setUiError("Select at least one answer before submitting.");
      return;
    }
    if (!question.is_multi_answer && selectedAnswers.length !== 1) {
      setUiError("Single-answer questions require exactly one answer.");
      return;
    }

    const activeWorkflowId = await ensureSession();
    if (!activeWorkflowId) {
      return;
    }

    const nextTranscript = appendAnsweredQuestion(
      transcript,
      question,
      selectedAnswers,
    );
    persistTranscript(activeWorkflowId, nextTranscript);

    const command = buildAnswerCommand(question, selectedAnswers);
    await sendWorkflowCommand(command, {
      busyMessage: "Submitting answer...",
    });
  }

  async function handleReviewSelection(sessionId: string) {
    await sendWorkflowCommand(
      {
        command_id: crypto.randomUUID(),
        kind: "LOAD_COMPLETED_QUIZ",
        session_id: sessionId,
        selected_answers: [],
      },
      { busyMessage: "Loading review..." },
    );
  }

  const composerEnabled = isComposerEnabled(snapshot);
  const displayedError = uiError ?? snapshot?.last_error ?? null;
  const availableActions =
    snapshot?.available_actions ?? ["NEW_QUIZ", "LOAD_COMPLETED_QUIZ"];

  if (authStatus !== "authenticated" || !email) {
    return (
      <div className="auth-shell">
        <div className="auth-panel">
          <p className="eyebrow">Quiz Agent by Mati</p>
          <h1>Welcome Tooploox!</h1>
          <p className="auth-copy">
            Sign in with your email and password.
          </p>
          {displayedError ? (
            <div className="error-banner" role="alert">
              {displayedError}
            </div>
          ) : null}
          <form className="auth-form" onSubmit={handleLoginSubmit}>
            <label className="field">
              <span>Email</span>
              <input
                autoComplete="username"
                onChange={(event) => setLoginEmail(event.currentTarget.value)}
                placeholder="you@example.com"
                type="email"
                value={loginEmail}
              />
            </label>
            <label className="field">
              <span>Password</span>
              <input
                autoComplete="current-password"
                onChange={(event) => setLoginPassword(event.currentTarget.value)}
                placeholder="Shared demo password"
                type="password"
                value={loginPassword}
              />
            </label>
            <button className="primary-button auth-button" disabled={busy} type="submit">
              {busy && busyLabel ? busyLabel : "Enter workspace"}
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <ChatLayout
      busy={busy}
      busyLabel={busyLabel}
      composerDisabled={!composerEnabled}
      composerHelper={composerDisabledReason(snapshot)}
      composerValue={composerText}
      email={email}
      errorMessage={displayedError}
      onComposerChange={setComposerText}
      onComposerSubmit={handleClarificationSubmit}
      onLogout={handleLogout}
      scrollToken={`${workflowId ?? "none"}:${transcript.length}:${snapshotSignature(
        snapshot,
      )}`}
    >
      {/* <AssistantBubble>
        <p><strong>Welcome to Quiz Agent!</strong> Turn any markdown document into a tailored quiz.</p>
        <p style={{ margin: "8px 0 0", color: "var(--text-muted)", fontSize: "0.92rem" }}>
          <strong>1.</strong> Enter a topic and markdown URL below<br />
          <strong>2.</strong> I'll ask a few questions to tailor difficulty and focus<br />
          <strong>3.</strong> Answer the quiz, then review your results
        </p>
      </AssistantBubble> */}

      {snapshot?.message &&
      !(snapshot.pending_prompt && snapshot.message === snapshot.pending_prompt.text) ? (
        <AssistantBubble text={snapshot.message} />
      ) : null}

      {transcript.map((entry) => {
        if (entry.kind === "assistant-message") {
          return <AssistantBubble key={entry.id} text={entry.text} />;
        }
        if (entry.kind === "user-message") {
          return <UserBubble key={entry.id} text={entry.text} />;
        }
        if (entry.kind === "result") {
          return <ResultCard key={entry.id} result={entry.result} interactive={false} />;
        }
        if (entry.kind === "review") {
          return <ReviewCard key={entry.id} review={entry.review} />;
        }
        if (entry.kind !== "answered-question") {
          return null;
        }
        const answeredEntry = entry;
        return (
          <QuizCard
            busy={false}
            interactive={false}
            key={entry.id}
            question={answeredEntry}
            selectedAnswers={answeredEntry.selectedAnswers}
          />
        );
      })}

      {!snapshot || snapshot.state === "MENU" ? (
        <SetupCard
          busy={busy}
          markdownUrl={markdownUrl}
          onLoadCompleted={handleLoadCompletedList}
          onMarkdownUrlChange={setMarkdownUrl}
          onStartQuiz={handleStartQuiz}
          onTopicChange={setTopic}
          topic={topic}
        />
      ) : null}

      {snapshot?.state === "PREPARING_SOURCE" ||
      snapshot?.state === "GENERATING_QUIZ" ? (
        <LoadingCard detail={snapshot.message} title="The workflow is preparing your next step." />
      ) : null}

      {snapshot?.state === "QUIZ_ACTIVE" && snapshot.current_question ? (
        <QuizCard
          busy={busy}
          interactive
          onSubmit={handleAnswerSubmit}
          onToggleAnswer={toggleAnswer}
          question={snapshot.current_question}
          selectedAnswers={selectedAnswers}
        />
      ) : null}

      {snapshot?.state === "RESULT_MENU" && snapshot.result ? (
        <ResultCard
          busy={busy}
          canLoadCompleted={availableActions.includes("LOAD_COMPLETED_QUIZ")}
          canQuit={availableActions.includes("QUIT")}
          canRegenerate={availableActions.includes("REGENERATE_LAST_TOPIC")}
          onLoadCompleted={handleLoadCompletedList}
          onNewQuiz={handleBackToMenu}
          onQuit={handleQuit}
          onRegenerate={handleRegenerate}
          result={snapshot.result}
        />
      ) : null}

      {snapshot?.state === "REVIEW_LIST" ? (
        <ReviewListCard
          busy={busy}
          onChoose={handleReviewSelection}
          sessions={snapshot.review_sessions ?? []}
        />
      ) : null}

      {snapshot?.state === "REVIEW_COMPLETED" && snapshot.completed_review ? (
        <ReviewCard review={snapshot.completed_review} interactive onBackToMenu={handleBackToMenu} busy={busy} />
      ) : null}

      {snapshot &&
      ["PREPARATION_FAILED", "GENERATION_FAILED", "ABANDONED"].includes(
        snapshot.state,
      ) ? (
        <FailureCard
          busy={busy}
          canBack={availableActions.includes("BACK_TO_MENU")}
          canQuit={availableActions.includes("QUIT")}
          detail={snapshot.last_error ?? snapshot.message}
          onBack={handleBackToMenu}
          onQuit={handleQuit}
          title={snapshot.message}
        />
      ) : null}
    </ChatLayout>
  );
}
