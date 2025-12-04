// src/app/(main)/(projects)/connections/[projectName]/[cloudPlatform]/dashboards/awsdashboard/recommendations/page.tsx

"use client";

import React, { useState, useRef, useEffect } from "react";
import { useParams } from "next/navigation";
import { NormalizedRecommendation, RecommendationFilters, AWS_RESOURCES } from "@/types/recommendations";
import { fetchRecommendationsWithFilters } from "@/lib/recommendations";
import { BACKEND } from "@/lib/api";
import { calculateDateRange } from "@/lib/dateUtils";

// SHARED COMPONENT IMPORTS
import RecommendationFilterBar from "@/components/recommendations/RecommendationFilterBar";
import RecommendationList from "@/components/recommendations/RecommendationList";

const AwsRecommendationsPage: React.FC = () => {
  const params = useParams();
  const projectId = params.projectName as string;
  const cloudPlatform = 'aws' as const; // Hardcoded for AWS page

  const [recommendations, setRecommendations] = useState<NormalizedRecommendation[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Generation tracking to invalidate stale requests
  const generationRef = useRef(0);
  const currentTaskIdRef = useRef<string | null>(null);

  const resourceOptions = AWS_RESOURCES;

  // Initialize filters with the first AWS resource type
  const [filters, setFilters] = useState<RecommendationFilters>({
    resourceType: resourceOptions[0]?.displayName || '',
    resourceId: '',
    resourceIdEnabled: false,
    startDate: undefined,
    endDate: undefined,
    dateRangePreset: 'last_week'
  });

  // Cancel backend task (non-blocking) - matches Azure implementation
  const cancelBackendTask = async (projectIdToCancel: string) => {
    const cancelUrl = `${BACKEND}/cancel-tasks/${projectIdToCancel}`;
    console.log(`🔄 [NO-AUTH] Starting FAST cancel request to: ${cancelUrl}`);

    // Try multiple methods to ensure the request gets through

    // Method 1: navigator.sendBeacon (most reliable for fire-and-forget)
    if (typeof navigator.sendBeacon === 'function') {
      const sent = navigator.sendBeacon(cancelUrl);
      console.log(`📡 [NO-AUTH] sendBeacon result: ${sent}`);
    }

    // Method 2: fetch as backup (with detailed logging)
    try {
      console.log(`🔄 [DEBUG] About to call fetch()...`);
      fetch(cancelUrl, {
        method: 'POST',
      }).then(response => {
        console.log(`✅ [NO-AUTH] fetch() got response: ${response.status}`);
        if (response.ok) {
          return response.json();
        }
        throw new Error(`HTTP ${response.status}`);
      }).then(data => {
        console.log(`✅ [NO-AUTH] Cancel completed: cancelled ${data.cancelled_count} task(s)`);
      }).catch(error => {
        console.error(`❌ [NO-AUTH] fetch() failed:`, error);
      });
      console.log(`⚡ [DEBUG] fetch() call initiated`);
    } catch (error) {
      console.error(`❌ [DEBUG] Exception calling fetch():`, error);
    }
  };

  const handleFetch = async () => {
    // Increment generation - this invalidates all previous requests
    generationRef.current += 1;
    const thisGeneration = generationRef.current;

    console.log(`🚀 [RESET-AWS] Starting analysis (generation ${thisGeneration})`);

    // Validation ensures analysis only runs if a resource type is selected.
    if (!filters.resourceType) {
      setError("Please select a Resource Type to analyze.");
      setRecommendations([]);
      return;
    }

    if (!filters.startDate || !filters.endDate) {
      setError("Please select a date range.");
      setRecommendations([]);
      return;
    }

    // Cancel previous backend task (non-blocking)
    if (currentTaskIdRef.current) {
      cancelBackendTask(projectId);
      currentTaskIdRef.current = null;
    }

    setIsLoading(true);
    setError(null);

    try {
      // Use AWS_RESOURCES and 'aws' platform
      const result = await fetchRecommendationsWithFilters(
        projectId,
        cloudPlatform,
        filters,
        undefined // No abort signal needed
      );

      // CRITICAL: Only process if this is still the current generation
      if (generationRef.current !== thisGeneration) {
        console.log(`⚠️  Ignoring response from old generation ${thisGeneration} (current: ${generationRef.current})`);
        return;
      }

      // Store task_id for cleanup
      if (result.taskId) {
        currentTaskIdRef.current = result.taskId;
        console.log(`📋 Task started: ${result.taskId}`);
      }

      setRecommendations(result.recommendations);
      console.log(`✅ Analysis complete (generation ${thisGeneration}): ${result.recommendations.length} recommendations`);
    } catch (err) {
      // Only show errors for current generation
      if (generationRef.current !== thisGeneration) {
        console.log(`⚠️  Ignoring error from old generation ${thisGeneration}`);
        return;
      }

      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("An unknown error occurred while fetching recommendations.");
      }
    } finally {
      // Only clear loading if still current generation
      if (generationRef.current === thisGeneration) {
        setIsLoading(false);
      }
    }
  };

  const handleReset = async () => {
    // Increment generation - this makes all in-flight requests obsolete
    generationRef.current += 1;

    // IMMEDIATELY stop loading indicator so user sees response
    setIsLoading(false);

    console.log(`🔄 [RESET-AWS] Reset clicked (new generation: ${generationRef.current})`);

    // CRITICAL: AWAIT the cancel request to ensure it completes before state updates
    if (currentTaskIdRef.current || projectId) {
      await cancelBackendTask(projectId);  // Wait for it to complete!
      currentTaskIdRef.current = null;
      console.log(`✅ [DEBUG] Cancel request completed, now clearing UI...`);
    }

    // Clear UI AFTER cancel request completes
    const defaultPreset = 'last_week';
    const dateRange = calculateDateRange(defaultPreset);

    setFilters({
      resourceType: resourceOptions[0]?.displayName || '',
      resourceId: '',
      resourceIdEnabled: false,
      startDate: dateRange?.startDate,
      endDate: dateRange?.endDate,
      dateRangePreset: defaultPreset
    });
    setRecommendations([]);
    setError(null);

    console.log(`✅ Reset complete - UI cleared, generation ${generationRef.current}`);
  };

  // Initialize dates on component mount based on default preset
  useEffect(() => {
    const dateRange = calculateDateRange(filters.dateRangePreset);
    if (dateRange && !filters.startDate && !filters.endDate) {
      setFilters(prev => ({
        ...prev,
        startDate: dateRange.startDate,
        endDate: dateRange.endDate
      }));
      console.log(`📅 Initialized dates for preset '${filters.dateRangePreset}': ${dateRange.startDate.toISOString().split('T')[0]} to ${dateRange.endDate.toISOString().split('T')[0]}`);
    }
  }, []); // Run only on mount

  return (
    <div className="p-8">
      <h1 className="text-cp-title-2xl font-cp-semibold mb-6 text-cp-blue">
        AWS Cost Optimization Recommendations
      </h1>

      {/* FILTER BAR UI (Uses shared component with date/button validation) */}
      <RecommendationFilterBar
        filters={filters}
        setFilters={setFilters}
        resourceOptions={resourceOptions}
        isLoading={isLoading}
        onRunAnalysis={handleFetch}
        onReset={handleReset}
        projectId={projectId}
        cloudPlatform={cloudPlatform}
      />

      {/* RESULTS DISPLAY */}
      {isLoading ? (
        <div className="p-8 text-center text-lg">Analyzing {filters.resourceType} data...</div>
      ) : error ? (
        <div className="p-8 text-center text-red-600 font-medium">Error: {error}</div>
      ) : recommendations.length === 0 ? (
        <div className="p-8 text-center bg-gray-50 border rounded-lg shadow-sm">
          <p className="text-cp-body text-gray-700">No optimization opportunities found for the selected filters.</p>
        </div>
      ) : (
        <RecommendationList recommendations={recommendations} />
      )}
    </div>
  );
};

export default AwsRecommendationsPage;