"""chat

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQL — из docs/schema.sql. Колонки перевода создаются, но пока не используются.
    op.execute(
        """
        CREATE TABLE conversations (
          id               BIGSERIAL PRIMARY KEY,
          guest_id         BIGINT NOT NULL REFERENCES users(id),
          provider_id      BIGINT NOT NULL REFERENCES users(id),
          last_message_at  TIMESTAMPTZ,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT conversations_not_self_chk CHECK (guest_id <> provider_id),
          CONSTRAINT conversations_pair_uq UNIQUE (guest_id, provider_id)
        )
        """
    )
    op.execute("CREATE INDEX conversations_provider_idx ON conversations (provider_id)")
    op.execute(
        """
        CREATE TABLE messages (
          id                   BIGSERIAL PRIMARY KEY,
          conversation_id      BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
          sender_id            BIGINT NOT NULL REFERENCES users(id),
          original_text        TEXT NOT NULL,
          original_language    VARCHAR(8),
          translated_text      TEXT,
          translated_language  VARCHAR(8),
          is_flagged           BOOLEAN NOT NULL DEFAULT false,
          read_at              TIMESTAMPTZ,
          created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX messages_conversation_idx ON messages (conversation_id, created_at)")
    op.execute(
        "CREATE INDEX messages_unread_idx ON messages (conversation_id) WHERE read_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE messages")
    op.execute("DROP TABLE conversations")
