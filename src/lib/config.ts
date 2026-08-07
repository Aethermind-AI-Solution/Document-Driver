import { z } from "zod";

const envSchema = z.object({
  NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
  PORT: z.coerce.number().default(8080),
  LOG_LEVEL: z.string().default("info"),
  DATABASE_URL: z.string().default("postgres://postgres:postgres@localhost:5432/booking_core"),
  SESSION_TTL_HOURS: z.coerce.number().int().positive().default(24),
  MAX_TOOL_RETRIES: z.coerce.number().int().min(0).max(5).default(2),
  TIMEZONE: z.string().default("Asia/Kolkata"),
  DOCTOR_ID: z.string().default("dr_sumit")
});

export type AppConfig = z.infer<typeof envSchema>;

export const config: AppConfig = envSchema.parse(process.env);
