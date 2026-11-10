import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
export function cn(...inputs: ClassValue[]) { return twMerge(clsx(inputs)); }
const variants = cva("button", { variants: { variant: { default: "button-primary", outline: "button-outline", ghost: "button-ghost" }, size: { default: "", small: "button-small", icon: "button-icon" } }, defaultVariants: { variant: "default", size: "default" } });
// shadcn/ui-style composable button, with Radix Slot and CVA variants.
export function Button({ className, variant, size, asChild = false, ...props }: React.ComponentProps<"button"> & VariantProps<typeof variants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : "button";
  return <Comp className={cn(variants({ variant, size, className }))} {...props} />;
}
