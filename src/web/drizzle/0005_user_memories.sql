CREATE TABLE "web"."user_memories" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"user_id" uuid NOT NULL,
	"content" text NOT NULL,
	"created_at" timestamp DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "web"."user_memories" ADD CONSTRAINT "user_memories_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "web"."users"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "user_memories_user_idx" ON "web"."user_memories" USING btree ("user_id");
