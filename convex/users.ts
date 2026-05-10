import { mutation, query } from "./_generated/server";
import { v } from "convex/values";

export const getUser = query({
  args: { telegramUserId: v.number() },
  handler: async (ctx, { telegramUserId }) => {
    return await ctx.db
      .query("users")
      .withIndex("by_telegram_user_id", (q) => q.eq("telegramUserId", telegramUserId))
      .unique();
  },
});

export const upsertUser = mutation({
  args: {
    telegramUserId: v.number(),
    telegramUsername: v.optional(v.string()),
  },
  handler: async (ctx, { telegramUserId, telegramUsername }) => {
    const existing = await ctx.db
      .query("users")
      .withIndex("by_telegram_user_id", (q) => q.eq("telegramUserId", telegramUserId))
      .unique();
    if (existing) {
      if (telegramUsername !== undefined && existing.telegramUsername !== telegramUsername) {
        await ctx.db.patch(existing._id, { telegramUsername });
      }
      return existing._id;
    }
    return await ctx.db.insert("users", {
      telegramUserId,
      telegramUsername,
      messageCount: 0,
    });
  },
});

export const setName = mutation({
  args: { telegramUserId: v.number(), name: v.string() },
  handler: async (ctx, { telegramUserId, name }) => {
    const existing = await ctx.db
      .query("users")
      .withIndex("by_telegram_user_id", (q) => q.eq("telegramUserId", telegramUserId))
      .unique();
    if (!existing) {
      return await ctx.db.insert("users", {
        telegramUserId,
        name,
        messageCount: 0,
      });
    }
    await ctx.db.patch(existing._id, { name });
    return existing._id;
  },
});

export const setMachine = mutation({
  args: { telegramUserId: v.number(), machine: v.string() },
  handler: async (ctx, { telegramUserId, machine }) => {
    const existing = await ctx.db
      .query("users")
      .withIndex("by_telegram_user_id", (q) => q.eq("telegramUserId", telegramUserId))
      .unique();
    if (!existing) {
      return await ctx.db.insert("users", {
        telegramUserId,
        machine,
        messageCount: 0,
      });
    }
    await ctx.db.patch(existing._id, { machine });
    return existing._id;
  },
});

export const incrementMessageCount = mutation({
  args: { telegramUserId: v.number() },
  handler: async (ctx, { telegramUserId }) => {
    const existing = await ctx.db
      .query("users")
      .withIndex("by_telegram_user_id", (q) => q.eq("telegramUserId", telegramUserId))
      .unique();
    if (!existing) {
      await ctx.db.insert("users", { telegramUserId, messageCount: 1 });
      return 1;
    }
    const next = (existing.messageCount ?? 0) + 1;
    await ctx.db.patch(existing._id, { messageCount: next });
    return next;
  },
});

export const clearAll = mutation({
  args: {},
  handler: async (ctx) => {
    let count = 0;
    for (const u of await ctx.db.query("users").collect()) {
      await ctx.db.delete(u._id);
      count++;
    }
    return count;
  },
});

export const setBio = mutation({
  args: { telegramUserId: v.number(), bio: v.string() },
  handler: async (ctx, { telegramUserId, bio }) => {
    const existing = await ctx.db
      .query("users")
      .withIndex("by_telegram_user_id", (q) => q.eq("telegramUserId", telegramUserId))
      .unique();
    if (!existing) {
      return await ctx.db.insert("users", {
        telegramUserId,
        bio,
        bioGeneratedAt: Date.now(),
        messageCount: 0,
      });
    }
    await ctx.db.patch(existing._id, { bio, bioGeneratedAt: Date.now() });
    return existing._id;
  },
});
