import type {
  CompletedQuizReviewView,
  QuestionView,
  ResultView,
  SessionSummaryView,
} from "../api/types";
import type { TranscriptEntry } from "../app/transcript";
import { selectedLabels } from "../app/transcript";

type AnsweredQuestionEntry = Extract<
  TranscriptEntry,
  { kind: "answered-question" }
>;

function isAnsweredQuestionEntry(
  question: QuestionView | AnsweredQuestionEntry,
): question is AnsweredQuestionEntry {
  return "kind" in question;
}

interface SetupCardProps {
  topic: string;
  markdownUrl: string;
  busy: boolean;
  onTopicChange: (value: string) => void;
  onMarkdownUrlChange: (value: string) => void;
  onStartQuiz: () => void;
  onLoadCompleted: () => void;
}

export function SetupCard({
  topic,
  markdownUrl,
  busy,
  onTopicChange,
  onMarkdownUrlChange,
  onStartQuiz,
  onLoadCompleted,
}: SetupCardProps) {
  return (
    <article className="surface-card card-accent">
      <p className="card-kicker">Setup</p>
      <h2>Start a new source-driven quiz</h2>
      <p className="card-copy">
        Give the agent a topic and a markdown source. The workflow will prepare
        the source, clarify intent, generate the quiz, and keep the run durable.
      </p>

      <label className="field">
        <span>Topic</span>
        <input
          onChange={(event) => onTopicChange(event.currentTarget.value)}
          placeholder="Pipecat"
          type="text"
          value={topic}
        />
      </label>

      <label className="field">
        <span>Markdown URL (optional — uses Wikipedia if empty)</span>
        <input
          onChange={(event) => onMarkdownUrlChange(event.currentTarget.value)}
          placeholder="https://github.com/pipecat-ai/pipecat/blob/main/README.md"
          type="url"
          value={markdownUrl}
        />
      </label>

      <div className="card-actions">
        <button className="primary-button" disabled={busy} onClick={onStartQuiz} type="button">
          New Quiz
        </button>
        <button className="ghost-button" disabled={busy} onClick={onLoadCompleted} type="button">
          Show results of previous Quizes
        </button>
      </div>
    </article>
  );
}

export function LoadingCard({
  title,
  detail,
}: {
  title: string;
  detail: string;
}) {
  return (
    <article className="surface-card status-card">
      <div className="spinner" aria-hidden="true" />
      <div>
        <p className="card-kicker">In progress</p>
        <h2>{title}</h2>
        <p className="card-copy">{detail}</p>
      </div>
    </article>
  );
}

interface QuizCardProps {
  question: QuestionView | AnsweredQuestionEntry;
  selectedAnswers: number[];
  busy: boolean;
  interactive: boolean;
  onToggleAnswer?: (answerIndex: number) => void;
  onSubmit?: () => void;
}

export function QuizCard({
  question,
  selectedAnswers,
  busy,
  interactive,
  onToggleAnswer,
  onSubmit,
}: QuizCardProps) {
  const questionId =
    isAnsweredQuestionEntry(question) ? question.questionId : question.question_id;
  const questionText =
    isAnsweredQuestionEntry(question) ? question.questionText : question.question_text;
  const options = question.options;
  const isMultiAnswer =
    isAnsweredQuestionEntry(question)
      ? question.isMultiAnswer
      : question.is_multi_answer;
  const position = question.position;
  const totalQuestions =
    isAnsweredQuestionEntry(question)
      ? question.totalQuestions
      : question.total_questions;

  return (
    <article className="surface-card quiz-card">
      <div className="question-progress">
        <span>
          Question {position} of {totalQuestions}
        </span>
        <span>{isMultiAnswer ? "Multi-answer" : "Single answer"}</span>
      </div>
      <h2>{questionText}</h2>
      <div className="options-list">
        {options.map((option, index) => {
          const checked = selectedAnswers.includes(index);
          return (
            <label className={`option-row${checked ? " option-row-selected" : ""}`} key={`${questionId}:${index}`}>
              <input
                checked={checked}
                disabled={!interactive || busy}
                name={questionId}
                onChange={() => onToggleAnswer?.(index)}
                type={isMultiAnswer ? "checkbox" : "radio"}
              />
              <span>{option}</span>
            </label>
          );
        })}
      </div>
      {interactive ? (
        <div className="card-actions">
          <button
            className="primary-button"
            disabled={busy || selectedAnswers.length === 0}
            onClick={onSubmit}
            type="button"
          >
            Submit answer
          </button>
        </div>
      ) : (
        <p className="selection-summary">
          Selected: {selectedLabels(options, selectedAnswers).join(", ") || "No answer"}
        </p>
      )}
    </article>
  );
}

type ResultCardProps =
  | {
      result: ResultView;
      interactive: false;
    }
  | {
      result: ResultView;
      interactive?: true;
      busy: boolean;
      canRegenerate: boolean;
      canLoadCompleted: boolean;
      canQuit: boolean;
      onNewQuiz: () => void;
      onRegenerate: () => void;
      onLoadCompleted: () => void;
      onQuit: () => void;
    };

export function ResultCard(props: ResultCardProps) {
  const { result } = props;
  return (
    <article className="surface-card result-card">
      <p className="card-kicker">Result</p>
      <h2>Weighted final score</h2>
      <div className="score-grid">
        <div>
          <span className="score-label">Score</span>
          <strong>{result.final_score.toFixed(2)} / 4.00</strong>
        </div>
        <div>
          <span className="score-label">Percentage</span>
          <strong>{result.final_score_pct.toFixed(2)}%</strong>
        </div>
        <div>
          <span className="score-label">Answered</span>
          <strong>
            {result.answered_count} / {result.total_questions}
          </strong>
        </div>
      </div>
      {props.interactive !== false ? (
        <div className="card-actions">
          <button className="primary-button" disabled={props.busy} onClick={props.onNewQuiz} type="button">
            New Quiz
          </button>
          <button
            className="ghost-button"
            disabled={props.busy || !props.canRegenerate}
            onClick={props.onRegenerate}
            type="button"
          >
            Regenerate Last Topic
          </button>
          <button
            className="ghost-button"
            disabled={props.busy || !props.canLoadCompleted}
            onClick={props.onLoadCompleted}
            type="button"
          >
            Show results of previous Quizes
          </button>
          <button className="ghost-button" disabled={props.busy || !props.canQuit} onClick={props.onQuit} type="button">
            Quit
          </button>
        </div>
      ) : null}
    </article>
  );
}

interface ReviewListCardProps {
  sessions: SessionSummaryView[];
  busy: boolean;
  onChoose: (sessionId: string) => void;
}

export function ReviewListCard({
  sessions,
  busy,
  onChoose,
}: ReviewListCardProps) {
  return (
    <article className="surface-card">
      <p className="card-kicker">Completed quizzes</p>
      <h2>Choose a quiz to review</h2>
      <div className="review-list">
        {sessions.length === 0 ? (
          <p className="card-copy">No completed quizzes are stored yet for this user.</p>
        ) : (
          sessions.map((session) => (
            <button
              className="review-list-item"
              disabled={busy}
              key={session.session_id}
              onClick={() => onChoose(session.session_id)}
              type="button"
            >
              <span>
                <strong>{session.topic}</strong>
                <small>{new Date(session.created_at).toLocaleString()}</small>
              </span>
              <span>
                {session.final_score_pct === null
                  ? session.status
                  : `${session.final_score_pct.toFixed(2)}%`}
              </span>
            </button>
          ))
        )}
      </div>
    </article>
  );
}

export function ReviewCard({
  review,
  interactive,
  onBackToMenu,
  busy,
}: {
  review: CompletedQuizReviewView;
  interactive?: boolean;
  onBackToMenu?: () => void;
  busy?: boolean;
}) {
  return (
    <article className="surface-card review-card">
      <p className="card-kicker">Completed review</p>
      <h2>{review.topic}</h2>
      <p className="card-copy">
        Final weighted score: {review.final_score.toFixed(2)} / 4.00
        {" · "}
        {review.final_score_pct.toFixed(2)}%
      </p>
      <div className="review-questions">
        {review.questions.map((question) => (
          <section className="review-question" key={question.question_id}>
            <header>
              <span>
                Question {question.position}
                {question.is_multi_answer ? " · Multi-answer" : ""}
              </span>
              <strong>{question.score.toFixed(2)} pts</strong>
            </header>
            <p>{question.question_text}</p>
            <div className="review-answer-row">
              <span>Selected</span>
              <strong>
                {selectedLabels(question.options, question.selected_answers).join(", ") ||
                  "No answer"}
              </strong>
            </div>
            <div className="review-answer-row">
              <span>Correct</span>
              <strong>
                {selectedLabels(question.options, question.correct_answers).join(", ")}
              </strong>
            </div>
          </section>
        ))}
      </div>
      {interactive && onBackToMenu ? (
        <div className="card-actions">
          <button className="primary-button" disabled={busy} onClick={onBackToMenu} type="button">
            Back to Menu
          </button>
        </div>
      ) : null}
    </article>
  );
}

interface FailureCardProps {
  title: string;
  detail: string;
  busy: boolean;
  canBack: boolean;
  canQuit: boolean;
  onBack: () => void;
  onQuit: () => void;
}

export function FailureCard({
  title,
  detail,
  busy,
  canBack,
  canQuit,
  onBack,
  onQuit,
}: FailureCardProps) {
  return (
    <article className="surface-card error-card">
      <p className="card-kicker">Attention needed</p>
      <h2>{title}</h2>
      <p className="card-copy">{detail}</p>
      <div className="card-actions">
        <button className="primary-button" disabled={busy || !canBack} onClick={onBack} type="button">
          Back to Menu
        </button>
        <button className="ghost-button" disabled={busy || !canQuit} onClick={onQuit} type="button">
          Quit
        </button>
      </div>
    </article>
  );
}

export function AssistantBubble({ text, children }: { text?: string; children?: React.ReactNode }) {
  return (
    <div className="message-row">
      <div className="avatar avatar-assistant">A</div>
      <article className="message-bubble assistant-bubble">{children ?? text}</article>
    </div>
  );
}

export function UserBubble({ text }: { text: string }) {
  return (
    <div className="message-row message-row-user">
      <article className="message-bubble user-bubble">{text}</article>
      <div className="avatar avatar-user">You</div>
    </div>
  );
}
