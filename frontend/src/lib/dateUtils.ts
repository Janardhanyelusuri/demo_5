// src/lib/dateUtils.ts

import { DateRangePreset } from "@/types/recommendations";

export interface DateRange {
    startDate: Date;
    endDate: Date;
}

/**
 * Get current UTC date at midnight (00:00:00)
 * This ensures consistency with backend calculations
 */
function getTodayUTC(): Date {
    const now = new Date();
    return new Date(Date.UTC(
        now.getUTCFullYear(),
        now.getUTCMonth(),
        now.getUTCDate(),
        0, 0, 0, 0
    ));
}

/**
 * Subtract days from a UTC date
 */
function subtractDaysUTC(date: Date, days: number): Date {
    const result = new Date(date);
    result.setUTCDate(result.getUTCDate() - days);
    return result;
}

/**
 * Subtract months from a UTC date
 */
function subtractMonthsUTC(date: Date, months: number): Date {
    const result = new Date(date);
    result.setUTCMonth(result.getUTCMonth() - months);
    return result;
}

/**
 * Subtract years from a UTC date
 */
function subtractYearsUTC(date: Date, years: number): Date {
    const result = new Date(date);
    result.setUTCFullYear(result.getUTCFullYear() - years);
    return result;
}

/**
 * Calculate date range based on preset option using UTC
 * All ranges are calculated relative to current UTC date
 * This ensures cache key consistency with backend pre-warming
 */
export function calculateDateRange(preset: DateRangePreset): DateRange | null {
    const today = getTodayUTC();

    switch (preset) {
        case 'today':
            return {
                startDate: today,
                endDate: today
            };

        case 'yesterday':
            const yesterday = subtractDaysUTC(today, 1);
            return {
                startDate: yesterday,
                endDate: yesterday
            };

        case 'last_week':
            return {
                startDate: subtractDaysUTC(today, 7),
                endDate: today
            };

        case 'last_month':
            return {
                startDate: subtractMonthsUTC(today, 1),
                endDate: today
            };

        case 'last_6_months':
            return {
                startDate: subtractMonthsUTC(today, 6),
                endDate: today
            };

        case 'last_year':
            return {
                startDate: subtractYearsUTC(today, 1),
                endDate: today
            };

        case 'custom':
            // Return null for custom - user will set dates manually
            return null;

        default:
            return null;
    }
}
