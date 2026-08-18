from abc import ABC, abstractmethod


class EmailSender(ABC):
    @abstractmethod
    def send(self, to: str, subject: str, body: str) -> None:
        raise NotImplementedError


class ConsoleEmailSender(EmailSender):
    """Dev-mode fallback: prints the email to the server's own log instead of
    actually sending it. This is the default until a real provider is configured —
    an explicit, visible stand-in rather than a silently broken email flow. A real
    provider (Resend, SMTP) is a separate implementation of the same interface to
    add later, once a domain/API key exists; nothing else in the app needs to
    change to swap it in.
    """

    def send(self, to: str, subject: str, body: str) -> None:
        print(f"\n[email] To: {to}\n[email] Subject: {subject}\n[email] {body}\n")


def load_email_sender() -> EmailSender:
    # No real provider is configured yet (per the plan: the user hasn't set up a
    # domain/email service). Always returns the console fallback for now — this
    # function is the single place a real provider gets wired in later.
    return ConsoleEmailSender()
