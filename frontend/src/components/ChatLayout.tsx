import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";

interface ChatLayoutProps {
  email: string;
  errorMessage: string | null;
  busy: boolean;
  busyLabel: string | null;
  composerDisabled: boolean;
  composerHelper: string;
  composerValue: string;
  onComposerChange: (value: string) => void;
  onComposerSubmit: () => void;
  onLogout: () => void;
  scrollToken: string;
  children: React.ReactNode;
}

export function ChatLayout({
  email,
  errorMessage,
  busy,
  busyLabel,
  composerDisabled,
  composerHelper,
  composerValue,
  onComposerChange,
  onComposerSubmit,
  onLogout,
  scrollToken,
  children,
}: ChatLayoutProps) {
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const [stickToBottom, setStickToBottom] = useState(true);

  useEffect(() => {
    const element = transcriptRef.current;
    if (
      !element ||
      !stickToBottom ||
      typeof element.scrollTo !== "function"
    ) {
      return;
    }
    element.scrollTo({
      top: element.scrollHeight,
      behavior: "smooth",
    });
  }, [scrollToken, stickToBottom]);

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (composerDisabled || busy) {
      return;
    }
    onComposerSubmit();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key !== "Enter" || event.shiftKey) {
      return;
    }
    event.preventDefault();
    if (!composerDisabled && !busy) {
      onComposerSubmit();
    }
  }

  return (
    <div className="app-frame">
      <header className="app-header">
        <p className="eyebrow">Temporal Quiz Agent</p>
        <div className="header-actions">
          <div className="identity-pill">{email}</div>
          <button className="ghost-button" onClick={onLogout} type="button">
            Logout
          </button>
        </div>
      </header>

      {errorMessage ? (
        <section className="error-banner" role="alert">
          {errorMessage}
        </section>
      ) : null}

      <main
        className="transcript"
        onScroll={(event) => {
          const element = event.currentTarget;
          const distanceToBottom =
            element.scrollHeight - element.scrollTop - element.clientHeight;
          setStickToBottom(distanceToBottom < 80);
        }}
        ref={transcriptRef}
      >
        {children}
      </main>

      <footer className="composer-shell">
        <form className="composer-form" onSubmit={handleSubmit}>
          <textarea
            aria-label="Message input"
            className="composer-input"
            disabled={composerDisabled || busy}
            onChange={(event) => onComposerChange(event.currentTarget.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              composerDisabled ? composerHelper : "Type your reply to the agent..."
            }
            rows={1}
            value={composerValue}
          />
          <button
            className="primary-button"
            disabled={composerDisabled || busy || !composerValue.trim()}
            type="submit"
          >
            {busy && busyLabel ? busyLabel : "Send"}
          </button>
        </form>
        <p className="composer-helper">{composerHelper}</p>
      </footer>
    </div>
  );
}
