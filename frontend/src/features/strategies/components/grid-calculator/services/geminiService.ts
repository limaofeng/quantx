import { GoogleGenerativeAI } from '@google/generative-ai';

import { type GridConfig, type GridResult } from '../types';

export const analyzeStrategyWithGemini = async (
  config: GridConfig,
  result: GridResult
): Promise<string> => {
  // Using public VITE_ prefix for environment variables in Vite projects if available
  const apiKey = 'AIzaSyB3hIHLg560-hxt21uasEDLpespJ7Fvkd0';

  if (!apiKey) {
    throw new Error(
      'Gemini API Key not configured. Please set VITE_GEMINI_API_KEY.'
    );
  }

  const genAI = new GoogleGenerativeAI(apiKey);
  const model = genAI.getGenerativeModel({ model: 'gemini-2.0-flash-exp' });

  const prompt = `
  Role: Senior Quantitative Trader and Risk Manager specializing in A-Shares.
  
  Task: Analyze the following Grid Trading setup and provide a concise risk assessment and tactical suggestions.
  
  Market Context:
  - Symbol: ${config.symbol}
  - Current Price: ${config.basePrice}
  - Grid Type: ${config.gridType} (Step Up: ${config.stepPctUp}%, Step Down: ${config.stepPctDown}%)
  - Range: +${config.nUp} grids up, -${config.nDown} grids down.
  
  Generated Strategy Data:
  - Base Price: ${result.basePrice}
  - Total Buy Grids: ${result.levels.filter(l => l.side === 'BUY').length}
  - Total Sell Grids: ${result.levels.filter(l => l.side === 'SELL').length}
  - Planned Capital Usage: ${result.guards.buyBudget.toFixed(2)}
  - Max Position Cap: ${result.guards.maxPositionValue.toFixed(2)}
  
  Please provide a JSON response with the following keys. 
  IMPORTANT: The values of "summary", "analysis", and "suggestions" MUST BE IN CHINESE (SIMPLIFIED).
  
  1. "summary": A 1-sentence summary of the strategy's aggressivenes (in Chinese).
  2. "risk_score": An integer 1-10 (10 is highest risk).
  3. "analysis": A short paragraph analyzing the grid density and distribution. Is it too tight? Is the asymmetry (Up/Down) appropriate for A-shares? (in Chinese)
  4. "suggestions": 3 bullet points on how to improve this specific setup. (in Chinese)

  Output MUST be valid JSON only.
  `;

  const aiResult = await model.generateContent(prompt);
  const response = await aiResult.response;
  return response.text() || '{}';
};
