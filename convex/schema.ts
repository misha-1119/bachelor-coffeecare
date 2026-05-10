import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  kb_entries: defineTable({
    entryId: v.string(),
    category: v.string(),
    keywords: v.array(v.string()),
    question: v.string(),
    answer: v.string(),
    model: v.string(),
  })
    .index("by_entry_id", ["entryId"])
    .index("by_category", ["category"])
    .index("by_model", ["model"]),

  categories: defineTable({
    name: v.string(),
  }).index("by_name", ["name"]),

  users: defineTable({
    telegramUserId: v.number(),
    telegramUsername: v.optional(v.string()),
    name: v.optional(v.string()),
    machine: v.optional(v.string()),
    bio: v.optional(v.string()),
    messageCount: v.number(),
    bioGeneratedAt: v.optional(v.number()),
  }).index("by_telegram_user_id", ["telegramUserId"]),
});
