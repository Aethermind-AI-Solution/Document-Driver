export function computeRoi(
  { monthlyVolume, costPerDoc, minutesPerDoc, stpRate }:
  { monthlyVolume: number; costPerDoc: number; minutesPerDoc: number; stpRate: number },
) {
  const savedDocs = Math.round(monthlyVolume * stpRate);
  return {
    savedDocs,
    monthlySaved: Math.round(savedDocs * costPerDoc),
    hoursSaved: Math.round((savedDocs * minutesPerDoc) / 60),
  };
}
