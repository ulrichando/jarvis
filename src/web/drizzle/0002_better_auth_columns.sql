ALTER TABLE "web"."accounts" ADD COLUMN "id_token" text;--> statement-breakpoint
ALTER TABLE "web"."accounts" ADD COLUMN "access_token_expires_at" timestamp;--> statement-breakpoint
ALTER TABLE "web"."accounts" ADD COLUMN "refresh_token_expires_at" timestamp;--> statement-breakpoint
ALTER TABLE "web"."accounts" ADD COLUMN "scope" text;--> statement-breakpoint
ALTER TABLE "web"."accounts" ADD COLUMN "updated_at" timestamp DEFAULT now() NOT NULL;--> statement-breakpoint
ALTER TABLE "web"."sessions" ADD COLUMN "updated_at" timestamp DEFAULT now() NOT NULL;--> statement-breakpoint
ALTER TABLE "web"."verifications" ADD COLUMN "updated_at" timestamp DEFAULT now() NOT NULL;
