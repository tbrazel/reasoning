// Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
import { createContext } from "react";
import { openLedgerEntry } from "./workbenchEvents";

export const RichTextPolicy = createContext({ onOpenLedger: openLedgerEntry, allowArtifacts: true, allowImages: true });
