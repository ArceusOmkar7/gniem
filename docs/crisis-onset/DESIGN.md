---
name: Crisis-onset outlook
description: Scoped record of the incumbent Operate-mode extension.
colors:
  muted-dark: "#a9b4c3"
  note-dark: "#fbbf24"
  muted-light: "#475569"
  note-light: "#92400e"
---

# Design System: Crisis-onset outlook

## Overview

This record covers only the existing crisis-onset card in Operate mode. It sits after the map and sentiment/threat stack, before live news and activity spikes. The experimental TCN forecasts an unusual-news-activity proxy. Its uncalibrated scores guide review; they are not probabilities of real-world crises.

## Colors

Muted tokens serve explanatory copy, dates, codes, and column labels. Note tokens identify “Experimental TCN” and “Review,” with separate dark and light theme values. The activity icon and Retry action inherit the application's cyber-blue accent; the card inherits its glass surface and low-contrast borders.

## Typography

The card uses the application font: a bold base-size heading, small descriptive text, and extra-small metadata. Scores and metric values use monospaced tabular numerals with three decimal places for comparison.

## Layout

Desktop presents two equal columns with a 24px gap: country scores and held-out evaluation. Below the medium breakpoint, these stack in reading order. Padding is 20px. The captured forecast shows five eligible country rows; rendering follows eligible, non-null response rows rather than a hard-coded count. Labels wrap while scores retain their width. The metrics container allows horizontal overflow.

The existing global sticky header/filter block consumes substantial mobile height and can obscure scrolled content. That application-level issue remains outside this extension's scope.

## Elevation & Depth

The incumbent glass panel and faint row separators organize content without additional component-specific shadows.

## Shapes

The card inherits rounded-xl corners; rows remain flat and separated by fine borders.

## Components

An explicitly labelled section contains an h2; evaluation uses an h3 and a semantic table with row headers. TCN, logistic, and activity-ratio baselines share AP, precision, and recall columns. Copy defines AP, validation-selected thresholds, and the spike proxy. Dates expose forecast freshness and horizon.

Loading and unavailable states use status announcements. Retry is a native button with a visible keyboard-focus outline. An empty eligible-region state is explicit.

## Do's and Don'ts

- Do retain the experimental label, score caveat, comparable metrics, and proxy definition.
- Don't present scores as calibrated crisis probabilities.
