import { mutation, query } from "./_generated/server";
import { v } from "convex/values";

export const listEntries = query({
  args: {},
  handler: async (ctx) => {
    return await ctx.db.query("kb_entries").collect();
  },
});

export const listCategories = query({
  args: {},
  handler: async (ctx) => {
    const rows = await ctx.db.query("categories").collect();
    return rows.map((r) => r.name);
  },
});

export const getByEntryId = query({
  args: { entryId: v.string() },
  handler: async (ctx, { entryId }) => {
    return await ctx.db
      .query("kb_entries")
      .withIndex("by_entry_id", (q) => q.eq("entryId", entryId))
      .unique();
  },
});

export const getByCategory = query({
  args: { category: v.string() },
  handler: async (ctx, { category }) => {
    return await ctx.db
      .query("kb_entries")
      .withIndex("by_category", (q) => q.eq("category", category))
      .collect();
  },
});

export const getByModel = query({
  args: { model: v.string() },
  handler: async (ctx, { model }) => {
    return await ctx.db
      .query("kb_entries")
      .withIndex("by_model", (q) => q.eq("model", model))
      .collect();
  },
});

export const listForModel = query({
  args: { model: v.string() },
  handler: async (ctx, { model }) => {
    const universal = await ctx.db
      .query("kb_entries")
      .withIndex("by_model", (q) => q.eq("model", "universal"))
      .collect();
    if (model === "universal") return universal;
    const specific = await ctx.db
      .query("kb_entries")
      .withIndex("by_model", (q) => q.eq("model", model))
      .collect();
    return [...specific, ...universal];
  },
});

export const listModels = query({
  args: {},
  handler: async (ctx) => {
    const rows = await ctx.db.query("kb_entries").collect();
    const models = new Set(rows.map((r) => r.model));
    return Array.from(models).sort();
  },
});

export const upsertEntry = mutation({
  args: {
    entryId: v.string(),
    category: v.string(),
    keywords: v.array(v.string()),
    question: v.string(),
    answer: v.string(),
    model: v.string(),
  },
  handler: async (ctx, args) => {
    const existing = await ctx.db
      .query("kb_entries")
      .withIndex("by_entry_id", (q) => q.eq("entryId", args.entryId))
      .unique();
    if (existing) {
      await ctx.db.patch(existing._id, args);
      return existing._id;
    }
    return await ctx.db.insert("kb_entries", args);
  },
});

export const upsertCategory = mutation({
  args: { name: v.string() },
  handler: async (ctx, { name }) => {
    const existing = await ctx.db
      .query("categories")
      .withIndex("by_name", (q) => q.eq("name", name))
      .unique();
    if (existing) return existing._id;
    return await ctx.db.insert("categories", { name });
  },
});

export const clearAll = mutation({
  args: {},
  handler: async (ctx) => {
    for (const e of await ctx.db.query("kb_entries").collect()) {
      await ctx.db.delete(e._id);
    }
    for (const c of await ctx.db.query("categories").collect()) {
      await ctx.db.delete(c._id);
    }
  },
});
