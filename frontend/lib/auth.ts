export type Role = "admin" | "reviewer" | "viewer";
export type User = { id: number; email: string; role: Role; is_active: boolean };

export const canUpload = (r: Role) => r === "admin" || r === "reviewer";
export const canReview = (r: Role) => r === "admin" || r === "reviewer";
export const isAdmin = (r: Role) => r === "admin";
