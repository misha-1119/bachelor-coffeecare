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
});
