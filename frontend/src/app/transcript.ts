import type {
  CompletedQuizReviewView,
  PromptView,
  QuestionView,
  ResultView,
} from "../api/types";

export type TranscriptEntry =
  | {
      id: string;
      kind: "assistant-message" | "user-message";
      text: string;
    }
  | {
      id: string;
      kind: "answered-question";
      questionId: string;
      questionText: string;
      options: string[];
      selectedAnswers: number[];
      isMultiAnswer: boolean;
      position: number;
      totalQuestions: number;
    }
  | {
      id: string;
      kind: "result";
      result: ResultView;
    }
  | {
      id: string;
      kind: "review";
      review: CompletedQuizReviewView;
    };

export function ensurePromptRecorded(
  transcript: TranscriptEntry[],
  prompt: PromptView,
): TranscriptEntry[] {
  if (transcript.some((entry) => entry.id === prompt.prompt_id)) {
    return transcript;
  }
  return [
    ...transcript,
    {
      id: prompt.prompt_id,
      kind: "assistant-message",
      text: prompt.text,
    },
  ];
}

export function appendClarificationReply(
  transcript: TranscriptEntry[],
  text: string,
): TranscriptEntry[] {
  return [
    ...transcript,
    {
      id: `reply:${crypto.randomUUID()}`,
      kind: "user-message",
      text,
    },
  ];
}

export function appendAnsweredQuestion(
  transcript: TranscriptEntry[],
  question: QuestionView,
  selectedAnswers: number[],
): TranscriptEntry[] {
  return [
    ...transcript,
    {
      id: `answer:${question.question_id}`,
      kind: "answered-question",
      questionId: question.question_id,
      questionText: question.question_text,
      options: question.options,
      selectedAnswers: [...selectedAnswers],
      isMultiAnswer: question.is_multi_answer,
      position: question.position,
      totalQuestions: question.total_questions,
    },
  ];
}

export function appendResult(
  transcript: TranscriptEntry[],
  result: ResultView,
  id: string,
): TranscriptEntry[] {
  if (transcript.some((entry) => entry.id === id)) {
    return transcript;
  }
  return [...transcript, { id, kind: "result", result }];
}

export function appendReview(
  transcript: TranscriptEntry[],
  review: CompletedQuizReviewView,
  id: string,
): TranscriptEntry[] {
  if (transcript.some((entry) => entry.id === id)) {
    return transcript;
  }
  return [...transcript, { id, kind: "review", review }];
}

export function selectedLabels(
  options: string[],
  selectedAnswers: number[],
): string[] {
  return selectedAnswers
    .map((index) => options[index])
    .filter((value): value is string => Boolean(value));
}
