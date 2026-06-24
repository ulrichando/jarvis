ALTER TABLE "web"."artifacts" ADD COLUMN "updated_at" timestamp DEFAULT now() NOT NULL;--> statement-breakpoint
ALTER TABLE "web"."artifacts" ADD COLUMN "share_token" text;--> statement-breakpoint
ALTER TABLE "web"."artifacts" ADD COLUMN "share_expires_at" timestamp;--> statement-breakpoint
ALTER TABLE "web"."artifacts" ADD CONSTRAINT "artifacts_share_token_unique" UNIQUE("share_token");
