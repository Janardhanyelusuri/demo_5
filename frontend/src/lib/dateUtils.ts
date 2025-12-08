// src/lib/dateUtils.ts

import { DateRangePreset } from "@/types/recommendations";

export interface DateRange {
    startDate: Date;
    endDate: Date;
}

/**
 * Get current IST date at midnight (00:00:00)
 * IST is UTC+5:30 (Indian Standard Time)
 * This ensures consistency with backend calculations
 */
function getTodayIST(): Date {
    const now = new Date();
    // Convert to IST by adding 5 hours and 30 minutes to UTC
    const utcTime = Date.UTC(
        now.getUTCFullYear(),
        now.getUTCMonth(),
        now.getUTCDate(),
        now.getUTCHours(),
        now.getUTCMinutes(),
        0, 0
    );
    const istTime = utcTime + (5 * 60 * 60 * 1000) + (30 * 60 * 1000); // +5:30
    const istDate = new Date(istTime);

    // Set to midnight IST
    return new Date(Date.UTC(
        istDate.getUTCFullYear(),
        istDate.getUTCMonth(),
        istDate.getUTCDate(),
        0, 0, 0, 0
    ));
}

/**
 * Subtract days from an IST date
 */
function subtractDaysIST(date: Date, days: number): Date {
    const result = new Date(date);
    result.setUTCDate(result.getUTCDate() - days);
    return result;
}

/**
 * Subtract months from an IST date
 */
function subtractMonthsIST(date: Date, months: number): Date {
    const result = new Date(date);
    result.setUTCMonth(result.getUTCMonth() - months);
    return result;
}

/**
 * Subtract years from an IST date
 */
function subtractYearsIST(date: Date, years: number): Date {
    const result = new Date(date);
    result.setUTCFullYear(result.getUTCFullYear() - years);
    return result;
}

/**
 * Calculate date range based on preset option using IST
 * All ranges are calculated relative to current IST date
 * This ensures cache key consistency with backend pre-warming
 */
export function calculateDateRange(preset: DateRangePreset): DateRange | null {
    const today = getTodayIST();

    switch (preset) {
        case 'today':
            return {
                startDate: today,
                endDate: today
            };

        case 'yesterday':
            const yesterday = subtractDaysIST(today, 1);
            return {
                startDate: yesterday,
                endDate: yesterday
            };

        case 'last_week':
            return {
                startDate: subtractDaysIST(today, 7),
                endDate: today
            };

        case 'last_month':
            return {
                startDate: subtractMonthsIST(today, 1),
                endDate: today
            };

        case 'last_6_months':
            return {
                startDate: subtractMonthsIST(today, 6),
                endDate: today
            };

        case 'last_year':
            return {
                startDate: subtractYearsIST(today, 1),
                endDate: today
            };

        case 'custom':
            // Return null for custom - user will set dates manually
            return null;

        default:
            return null;
    }
}
