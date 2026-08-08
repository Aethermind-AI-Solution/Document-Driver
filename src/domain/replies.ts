import { hasDevanagari } from "../lib/time.js";

export type Language = "en" | "hi";

export function chooseLanguage(text: string, fallback: Language = "en"): Language {
  if (hasDevanagari(text)) {
    return "hi";
  }
  return fallback;
}

export const Replies = {
  askName: (lang: Language) =>
    lang === "hi"
      ? "Kripya apna poora naam batayein."
      : "Could I please get your full name?",
  askMobile: (lang: Language, name: string) =>
    lang === "hi"
      ? `Dhanyavaad ${name}. Kripya apna 10-digit mobile number share karein.`
      : `Thank you, ${name}. Could I please have your 10-digit mobile number?`,
  askDate: (lang: Language) =>
    lang === "hi"
      ? "Aap kis date ko appointment chahte hain? (DD-MM-YYYY)"
      : "What date would you like to visit Dr. Sumit? (DD-MM-YYYY)",
  rejectPastOrSameDay: (lang: Language) =>
    lang === "hi"
      ? "Same-day ya past date booking allowed nahi hai. Kripya kal ya uske baad ki date batayein."
      : "I apologize, but we do not accept same-day or past bookings. Could I book you for tomorrow or a later date?",
  askPeriod: (lang: Language) =>
    lang === "hi"
      ? "Kya aap morning slot chahenge ya evening slot?"
      : "Would you prefer a morning or an evening appointment?",
  noSlotsForPeriod: (lang: Language) =>
    lang === "hi"
      ? "Is preference mein slot available nahi hai. Kripya doosra time preference batayein."
      : "No slots are available for this preference. Please choose the other half of the day.",
  chooseSlot: (lang: Language, slots: string[]) =>
    lang === "hi"
      ? `Kripya in mein se slot choose karein: ${slots.join(", ")}.`
      : `Please choose from the following available slots: ${slots.join(", ")}.`,
  invalidSlot: (lang: Language) =>
    lang === "hi"
      ? "Valid slot format bhejiye, jaise 06:40 PM."
      : "Please send a valid slot like 06:40 PM.",
  askConfirmation: (lang: Language, summary: string) =>
    lang === "hi"
      ? `${summary}\nKya main isse confirm kar doon?`
      : `${summary}\nShall I confirm this for you?`,
  confirmationComplete: (lang: Language, name: string, mobile: string, date: string, time: string) =>
    lang === "hi"
      ? `✅ Aapka appointment confirm ho gaya hai!\n\n👤 Patient Name: ${name}\n📞 Mobile Number: ${mobile}\n📅 Booking Date: ${date}\n🕐 Booking Time: ${time}\n\n📍 Dr. Sumit Homeopathy Clinic, UFF-16A Ecomart, Supertech Ecovillage 1, Greater Noida`
      : `✅ Your appointment is confirmed!\n\n👤 Patient Name: ${name}\n📞 Mobile Number: ${mobile}\n📅 Booking Date: ${date}\n🕐 Booking Time: ${time}\n\n📍 Dr. Sumit Homeopathy Clinic, UFF-16A Ecomart, Supertech Ecovillage 1, Greater Noida`,
  confirmationDeclined: (lang: Language) =>
    lang === "hi"
      ? "Theek hai, chaliye date/time dobara select karte hain."
      : "No problem. Let us pick another date/time.",
  toolFailureFallback: (lang: Language) =>
    lang === "hi"
      ? "System delay hai. Aapka request queue mein daal diya gaya hai, hum jaldi confirm karenge."
      : "We are facing a temporary system delay. Your request is queued and we will confirm shortly.",
  nonTextFallback: (lang: Language) =>
    lang === "hi"
      ? "Text message bhejiye taaki main booking process continue kar sakun."
      : "Please send a text message so I can continue your booking.",
  cancelAck: (lang: Language) =>
    lang === "hi" ? "Appointment cancel request receive ho gaya hai." : "Your cancellation request has been received.",
  rescheduleAck: (lang: Language) =>
    lang === "hi" ? "Reschedule request receive ho gaya hai." : "Your reschedule request has been received."
};
