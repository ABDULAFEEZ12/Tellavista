// Initialize conversation history array
let chatHistory = [];

function switchTab(sectionId, event) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById(sectionId).classList.add('active');
  event.target.classList.add('active');
  // Clear previous messages/results
  document.getElementById('messages').innerHTML = '';
  document.getElementById('quranResult').innerHTML = '';
  document.getElementById('hadithResult').innerHTML = '';
}

function toggleMode() {
  document.body.classList.toggle('light-mode');
  document.body.classList.toggle('dark-mode');
  const toggleBtn = document.querySelector('.toggle-btn');
  if (document.body.classList.contains('dark-mode')) {
    toggleBtn.textContent = 'Toggle Light Mode';
  } else {
    toggleBtn.textContent = 'Toggle Dark Mode';
  }
}

async function sendQuestion() {
  const inputEl = document.getElementById("userInput");
  const question = inputEl.value.trim();
  if (!question) return;

  // Show user's message
  const messagesDiv = document.getElementById("messages");
  const userMsg = document.createElement("p");
  userMsg.className = "user-message";
  userMsg.textContent = "You: " + question;
  messagesDiv.appendChild(userMsg);
  inputEl.value = "";
  messagesDiv.scrollTop = messagesDiv.scrollHeight;

  // Add user message to chat history
  chatHistory.push({ role: "user", content: question });

  // Show loading indicator
  const loadingMsg = document.createElement("p");
  loadingMsg.className = "loading-indicator";
  loadingMsg.textContent = "Tawfiq AI is thinking...";
  messagesDiv.appendChild(loadingMsg);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;

  try {
    const response = await fetch('/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ history: chatHistory })
    });
    const data = await response.json();
    // Remove loading
    messagesDiv.removeChild(loadingMsg);
    const answerText = data.answer || "Sorry, couldn't answer.";
    // Append AI reply to chat history
    chatHistory.push({ role: "assistant", content: answerText });
    // Display AI reply
    const aiMsg = document.createElement("p");
    aiMsg.innerHTML = "Tawfiq AI: " + answerText.replace(/\n/g, '<br>');
    messagesDiv.appendChild(aiMsg);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
  } catch (error) {
    console.error('Error:', error);
    messagesDiv.removeChild(loadingMsg);
    const errorMsg = document.createElement("p");
    errorMsg.textContent = "Tawfiq AI: Error getting response.";
    messagesDiv.appendChild(errorMsg);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
  }
}

// Quran search logic
function searchQuran() {
  const query = document.getElementById("quranInput").value.trim() || document.getElementById("surahList").value;
  if (!query) return;
  const resultDiv = document.getElementById("quranResult");
  const quranSearchBtn = document.getElementById("quranSearchButton");
  resultDiv.innerHTML = `<div class="user-message">Searching: ${query}</div>`;
  const loading = document.createElement("div");
  loading.className = "loading-indicator";
  loading.textContent = "Searching Quran...";
  resultDiv.appendChild(loading);
  quranSearchBtn.disabled = true;

  fetch('/quran-search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query })
  }).then(res => res.json()).then(data => {
    resultDiv.innerHTML = `<div class="user-message">Searching: ${query}</div>`;
    if (data.surah_title && data.results && Array.isArray(data.results) && data.results.length > 0) {
      const titleEl = document.createElement("div");
      titleEl.innerHTML = `<strong>${data.surah_title}:</strong>`;
      titleEl.style.fontWeight = 'bold';
      titleEl.style.marginBottom = '10px';
      resultDiv.appendChild(titleEl);
      data.results.forEach(verse => {
        const verseEl = document.createElement("div");
        verseEl.className = "quran-verse";

        const info = document.createElement("p");
        info.innerHTML = `<strong>${verse.surah_number}:${verse.verse_number}</strong>`;
        verseEl.appendChild(info);

        const trans = document.createElement("p");
        trans.textContent = verse.translation;
        verseEl.appendChild(trans);

        const arabic = document.createElement("p");
        arabic.textContent = verse.arabic_text;
        arabic.style.textAlign = 'right';
        arabic.style.fontWeight = 'bold';
        arabic.style.marginTop = '5px';
        arabic.style.direction = 'rtl';
        verseEl.appendChild(arabic);

        if (verse.transliteration) {
          const translitPara = document.createElement("p");
          translitPara.innerHTML = `<em>Transliteration:</em> ${verse.transliteration}`;
          translitPara.style.marginTop = '5px';
          translitPara.style.fontSize = '0.95em';
          verseEl.appendChild(translitPara);
        }

        resultDiv.appendChild(verseEl);
      });
    } else if (data.result) {
      resultDiv.innerHTML += `<div>${data.result}</div>`;
    } else {
      resultDiv.innerHTML += `<div>No result found for "${query}".</div>`;
    }
  }).catch(error => {
    console.error('Error:', error);
    resultDiv.innerHTML += `<div>Error searching Quran.</div>`;
  }).finally(() => {
    const loadingEl = resultDiv.querySelector('.loading-indicator');
    if (loadingEl) loadingEl.remove();
    quranSearchBtn.disabled = false;
  });
}

// Hadith search logic
function searchHadith() {
  const inputQuery = document.getElementById("hadithInput").value.trim();
  const dropdownQuery = document.getElementById("hadithDropdown").value;
  const query = inputQuery || dropdownQuery;
  if (!query) return;
  const resultDiv = document.getElementById("hadithResult");
  const hadithBtn = document.getElementById("hadithSearchButton");
  resultDiv.innerHTML = `<div class="user-message">Searching: ${query}</div>`;
  const loading = document.createElement("div");
  loading.className = "loading-indicator";
  loading.textContent = "Searching Hadith...";
  resultDiv.appendChild(loading);
  hadithBtn.disabled = true;

  fetch('/hadith-search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query })
  }).then(res => res.json()).then(data => {
    resultDiv.innerHTML = `<div class="user-message">Searching: ${query}</div>`;
    if (data.results && Array.isArray(data.results) && data.results.length > 0) {
      data.results.forEach(hadith => {
        const hadithEl = document.createElement("div");
        hadithEl.className = "hadith-entry";

        let infoText = '';
        if (hadith.book_name && hadith.book_number && hadith.book_name !== 'Unknown Book' && hadith.book_number !== 'N/A') {
          infoText += `<strong>Book:</strong> ${hadith.book_name} (${hadith.book_number})`;
        } else if (hadith.book_name && hadith.book_name !== 'Unknown Book') {
          infoText += `<strong>Book:</strong> ${hadith.book_name}`;
        } else if (hadith.book_number && hadith.book_number !== 'N/A') {
          infoText += `<strong>Book Number:</strong> ${hadith.book_number}`;
        }
        if (hadith.volume_number && hadith.volume_number !== 'N/A') {
          infoText += (infoText ? '<br>' : '') + `<strong>Volume:</strong> ${hadith.volume_number}`;
        }
        if (hadith.hadith_info && hadith.hadith_info !== 'Volume N/A, Book N/A') {
          if (!infoText.includes(hadith.hadith_info))
            infoText += (infoText ? '<br>' : '') + `<strong>Info:</strong> ${hadith.hadith_info}`;
        }
        if (hadith.narrator && hadith.narrator !== 'Unknown narrator') {
          infoText += (infoText ? '<br>' : '') + `<strong>Narrated by:</strong> ${hadith.narrator}`;
        }

        const infoPara = document.createElement("p");
        infoPara.innerHTML = infoText;
        hadithEl.appendChild(infoPara);

        const textPara = document.createElement("p");
        textPara.textContent = hadith.text;
        textPara.style.marginTop = '8px';
        hadithEl.appendChild(textPara);

        resultDiv.appendChild(hadithEl);
      });
    } else if (data.result) {
      resultDiv.innerHTML += `<div>${data.result}</div>`;
    } else {
      resultDiv.innerHTML += `<div>No result found for "${query}".</div>`;
    }
  }).catch(error => {
    console.error('Error:', error);
    resultDiv.innerHTML += `<div>Error searching Hadith.</div>`;
  }).finally(() => {
    document.getElementById("hadithSearchButton").disabled = false;
  });
}

// Load Surah list from backend
function loadSurahList() {
  fetch('/get-surah-list')
    .then(res => res.json())
    .then(data => {
      const select = document.getElementById("surahList");
      select.innerHTML = '<option value="">-- Or Select Surah --</option>';
      if (data.surahs && Array.isArray(data.surahs)) {
        data.surahs.forEach(surah => {
          const option = document.createElement("option");
          option.value = surah;
          option.textContent = surah;
          select.appendChild(option);
        });
      } else {
        loadHardcodedSurahList();
      }
    }).catch(error => {
      console.error('Error loading Surah list:', error);
      loadHardcodedSurahList();
    });
}

function loadHardcodedSurahList() {
  const surahs = [
    "Al-Fatihah", "Al-Baqarah", "Aali Imran", "An-Nisa", "Al-Maidah", "Al-Anam", "Al-Araf", "Al-Anfal",
    "At-Tawbah", "Yunus", "Hud", "Yusuf", "Ar-Rad", "Ibrahim", "Al-Hijr", "An-Nahl", "Al-Isra",
    "Al-Kahf", "Maryam", "Ta-Ha", "Al-Anbiya", "Al-Hajj", "Al-Muminun", "An-Nur", "Al-Furqan",
    "Ash-Shuara", "An-Naml", "Al-Qasas", "Al-Ankabut", "Ar-Rum", "Luqman", "As-Sajda", "Al-Ahzab",
    "Saba", "Fatr", "Ya-Sin", "As-Saffat", "Sad", "Az-Zumar", "Ghafir", "Fussilat", "Ash-Shura",
    "Az-Zukhruf", "Ad-Dukhan", "Al-Jathiya", "Al-Ahqaf", "Muhammad", "Al-Fath", "Al-Hujurat", "Qaf",
    "Adh-Dhariyat", "At-Tur", "An-Najm", "Al-Qamar", "Ar-Rahman", "Al-Waqi'a", "Al-Hadid", "Al-Mujadila",
    "Al-Hashr", "Al-Mumtahanah", "As-Saff", "Al-Jumu'a", "Al-Munafiqun", "At-Taghabun", "At-Talaq",
    "At-Tahrim", "Al-Mulk", "Al-Qalam", "Al-Haqqah", "Al-Ma'arij", "Nuh", "Al-Jinn", "Al-Muzzammil",
    "Al-Muddathir", "Al-Qiyama", "Al-Insan", "Al-Mursalat", "An-Naba", "An-Nazi'at", "Abasa",
    "At-Takwir", "Al-Infitar", "Al-Mutaffifin", "Al-Inshiqaq", "Al-Buruj", "At-Tariq", "Al-Ala",
    "Al-Ghashiyah", "Al-Fajr", "Al-Balad", "Ash-Shams", "Al-Lail", "Ad-Duha", "Ash-Sharh", "At-Tin",
    "Al-Alaq", "Al-Qadr", "Al-Bayyina", "Az-Zalzalah", "Al-Adiyat", "Al-Qari'a", "At-Takathur",
    "Al-Asr", "Al-Humazah", "Al-Fil", "Quraysh", "Al-Ma'un", "Al-Kawthar", "Al-Kafirun", "An-Nasr",
    "Al-Masad", "Al-Ikhlas", "Al-Falaq", "Al-Nas"
  ];
  const select = document.getElementById("surahList");
  surahs.forEach(surah => {
    const option = document.createElement("option");
    option.value = surah;
    option.textContent = surah;
    select.appendChild(option);
  });
}

// Load Hadith topics
function loadHadithTopics() {
  const topics = [
    "Hadith on Prayer", "Hadith on Faith", "Hadith on Charity", "Hadith on Patience", "Hadith on Gratitude",
    // ... (rest of topics)
    "Hadith on Family Ties"
  ];
  const dropdown = document.getElementById("hadithDropdown");
  topics.forEach(topic => {
    const option = document.createElement("option");
    option.value = topic;
    option.textContent = topic;
    dropdown.appendChild(option);
  });
}

// Fetch and load the daily dua from JSON file
function loadDailyDuaFromJSON() {
  fetch('data/daily_duas.json')
    .then(res => res.json())
    .then(data => {
      // Expect data to be an object with a "dua" key
      const duaText = data.dua || "No Dua available.";
      document.getElementById('dailyDuaContent').innerHTML = `<p>"${duaText}"</p>`;
    })
    .catch(error => {
      document.getElementById('dailyDuaContent').innerHTML = '<p>Unable to load Dua for today.</p>';
      console.error('Error loading daily_duas.json:', error);
    });
}

// Initialize on DOMContentLoaded
document.addEventListener("DOMContentLoaded", () => {
  loadHadithTopics();
  loadSurahList();
  loadDailyDuaFromJSON(); // Load the Dua from JSON on page load
});