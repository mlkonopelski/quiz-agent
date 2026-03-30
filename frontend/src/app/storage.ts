import type { TranscriptEntry } from "./transcript";

const LAST_EMAIL_KEY = "quiz-agent:last-email";

function workflowKey(email: string): string {
  return `quiz-agent:workflow:${email}`;
}

function transcriptKey(workflowId: string): string {
  return `quiz-agent:transcript:${workflowId}`;
}

export function loadLastEmail(): string {
  return window.localStorage.getItem(LAST_EMAIL_KEY) ?? "";
}

export function saveLastEmail(email: string): void {
  window.localStorage.setItem(LAST_EMAIL_KEY, email);
}

export function loadWorkflowId(email: string): string | null {
  return window.localStorage.getItem(workflowKey(email));
}

export function saveWorkflowId(email: string, workflowId: string): void {
  window.localStorage.setItem(workflowKey(email), workflowId);
}

export function clearWorkflowId(email: string): void {
  window.localStorage.removeItem(workflowKey(email));
}

export function loadTranscript(workflowId: string): TranscriptEntry[] {
  const raw = window.localStorage.getItem(transcriptKey(workflowId));
  if (!raw) {
    return [];
  }

  try {
    const parsed = JSON.parse(raw) as TranscriptEntry[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveTranscript(
  workflowId: string,
  transcript: TranscriptEntry[],
): void {
  window.localStorage.setItem(
    transcriptKey(workflowId),
    JSON.stringify(transcript),
  );
}

export function clearTranscript(workflowId: string): void {
  window.localStorage.removeItem(transcriptKey(workflowId));
}
