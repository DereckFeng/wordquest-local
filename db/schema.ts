import { sql } from "drizzle-orm";
import { integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const students = sqliteTable("students", {
  id: text("id").primaryKey(),
  username: text("username").notNull().unique(),
  displayName: text("display_name").notNull(),
  passwordHash: text("password_hash").notNull(),
  passwordSalt: text("password_salt").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const sessions = sqliteTable("sessions", {
  token: text("token").primaryKey(),
  userId: text("user_id").notNull(),
  expiresAt: integer("expires_at").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const learningState = sqliteTable("student_learning_state", {
  userId: text("user_id").primaryKey(),
  stateJson: text("state_json").notNull(),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const courseLibrary = sqliteTable("course_library", {
  id: text("id").primaryKey(),
  level: text("level").notNull(),
  title: text("title").notNull(),
  titleZh: text("title_zh").notNull().default(""),
  sentencesJson: text("sentences_json").notNull(),
  sourceName: text("source_name").notNull().default("local import"),
  importedAt: text("imported_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});
