export type CommandKind =
  | "NEW_QUIZ"
  | "REPLY_CLARIFICATION"
  | "ANSWER_QUESTION"
  | "REGENERATE_LAST_TOPIC"
  | "LOAD_COMPLETED_QUIZ"
  | "BACK_TO_MENU"
  | "QUIT";

export interface CommandEnvelope {
  command_id: string;
  kind: CommandKind;
  correlation_id?: string | null;
  topic?: string | null;
  markdown_url?: string | null;
  session_id?: string | null;
  text?: string | null;
  selected_answers?: number[];
}

export interface AuthSessionResponse {
  email: string;
}

export interface CreateSessionResponse {
  workflow_id: string;
}

export interface PromptView {
  prompt_id: string;
  text: string;
  turn_no: number;
}

export interface QuestionView {
  question_id: string;
  question_text: string;
  options: string[];
  is_multi_answer: boolean;
  position: number;
  total_questions: number;
}

export interface ResultView {
  final_score: number;
  final_score_pct: number;
  answered_count: number;
  total_questions: number;
}

export interface SessionSummaryView {
  session_id: string;
  topic: string;
  status: string;
  final_score_pct: number | null;
  created_at: string;
}

export interface CompletedQuestionReviewView {
  question_id: string;
  question_text: string;
  options: string[];
  selected_answers: number[];
  correct_answers: number[];
  is_multi_answer: boolean;
  position: number;
  score: number;
  is_correct: boolean;
}

export interface CompletedQuizReviewView {
  session_id: string;
  topic: string;
  questions: CompletedQuestionReviewView[];
  final_score: number;
  final_score_pct: number;
}

export interface WorkflowSnapshot {
  state: string;
  message: string;
  pending_prompt: PromptView | null;
  current_question: QuestionView | null;
  result: ResultView | null;
  review_sessions: SessionSummaryView[] | null;
  completed_review: CompletedQuizReviewView | null;
  available_actions: string[];
  last_error: string | null;
}
