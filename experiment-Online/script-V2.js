// Matches setup/sequences/items_mapping.json's stim_IDs; used to convert
// the string icon names in data/practice_sequences.csv and
// data/session_1.csv into a numeric ID scheme.
let imageMappings = {
  0: "and",
  1: "angle-up",
  2: "asterisk",
  3: "at",
  4: "dollar-sign",
  5: "equals",
  6: "exclamation",
  7: "greater-than",
  8: "hashtag",
  9: "less-than",
  10: "minus",
  11: "percent",
  12: "plus",
  13: "quote",
  14: "slash",
  15: "tilde",
};

let nameToId = Object.fromEntries(
  Object.entries(imageMappings).map(([id, name]) => [name, parseInt(id)])
);

//  * ############### CONFIGURATION ###############
let practiceDataPath = "data/practice_sequences.csv";
let sessionDataPath = "data/session_1.csv";

let config = {
  debugLvl: 1,
  serverEndpoint:
    "https://3af36fcb-1736-4dde-8674-e8c00154e141.mock.pstmn.io/data",
  // Matches experiment-Lab/experiment.py's timings.feedback_duration
  // (config/experiment_config.json), shown after each practice-trial answer.
  feedbackTime: 4000,
  // Matches experiment-Lab/experiment.py's timings.resp_window
  // (config/experiment_config.json): 12 seconds to respond per trial.
  maxRespTime: 12000,
  postPracticeDelay: 3000,
  validKeys: ["a", "x", "m", "l"],
  // Matches experiment-Lab/experiment.py's block_size: 220 trials / 20 =
  // 11 blocks, i.e. 10 rest breaks between them.
  blockSize: 20,
};

// The 16 symbols from imageMappings above; question-mark.png is loaded
// separately as questionMarkImg since it's used as a mask placeholder, not
// a content icon.
let imagePaths = [
  "images/resized/and.png",
  "images/resized/angle-up.png",
  "images/resized/asterisk.png",
  "images/resized/at.png",
  "images/resized/dollar-sign.png",
  "images/resized/equals.png",
  "images/resized/exclamation.png",
  "images/resized/greater-than.png",
  "images/resized/hashtag.png",
  "images/resized/less-than.png",
  "images/resized/minus.png",
  "images/resized/percent.png",
  "images/resized/plus.png",
  "images/resized/quote.png",
  "images/resized/slash.png",
  "images/resized/tilde.png",
];

// let blankImage = "images/blank_image.png"; // ! file doesn't exist -> 404 (harmless, img opacity is 0)
let questionMarkImg = "images/resized/question-mark.png";
// imagePaths = imagePaths.concat(["images/blank_image.png", "images/resized/question-mark.png"]);
// console.log(imagePaths);

let images = preloadImages(imagePaths);
// let blankImage = images["blank_image"];
// let questionMarkImg = images["question-mark"];

log(4, images);

let midRowText = document.getElementById("mid-row-text");
let topRowId = "top-row";
let midRowId = "middle-row";
let bottomRowId = "bottom-row";

let responses = {
  practice: [],
  main: [],
};

// * ############### FUNCTIONS ###############
function log(lvl, ...message) {
  if (lvl <= config.debugLvl) {
    console.log(...message);
  }
}

function processSequences(sequences, imageMappings) {
  let processedSequences = [];
  for (let sequence of sequences) {
    // Dynamically get top images
    let topImages = [];

    for (let i = 1; i <= 8; i++) {
      topImages.push(sequence[`figure${i}`]);
    }

    topImages = topImages.map((i) => imageMappings[i]); // Map through imageMappings

    // Dynamically get bottom images
    let bottomImages = [];
    for (let i = 1; i <= 4; i++) {
      bottomImages.push(sequence[`choice${i}`]);
    }
    bottomImages = bottomImages.map((i) => imageMappings[i]); // Map through imageMappings

    let sequenceData = {
      ID: sequence.itemid,
      topImages: topImages,
      bottomImages: bottomImages,
      solution: imageMappings[sequence.solution],
      maskedImageIdx: sequence.maskedImageIdx,
      pattern: sequence.pattern,
    };
    processedSequences.push(sequenceData);
  }
  return processedSequences;
}

async function loadCSV(path) {
  const response = await fetch(path);
  const text = await response.text();
  const lines = text.trim().split("\n");
  const headers = lines[0].split(",");
  return lines.slice(1).map((line) => {
    const values = line.split(",");
    const row = {};
    headers.forEach((header, i) => (row[header] = values[i]));
    return row;
  });
}

// * Converts one CSV row (icon names as strings, from practice_sequences.csv
// * or session_1.csv) into a numeric-ID sequence object, for
// * processSequences(sequences, imageMappings) above.
function csvRowToSequence(row, nameToId) {
  let sequence = { itemid: parseInt(row.item_id) };
  for (let i = 1; i <= 8; i++) {
    sequence[`figure${i}`] = nameToId[row[`figure${i}`]];
  }
  for (let i = 1; i <= 4; i++) {
    sequence[`choice${i}`] = nameToId[row[`choice${i}`]];
  }
  sequence.solution = nameToId[row.solution];
  sequence.maskedImageIdx = parseInt(row.masked_idx);
  sequence.pattern = row.pattern;
  return sequence;
}

function chunk(array, size) {
  let chunks = [];
  for (let i = 0; i < array.length; i += size) {
    chunks.push(array.slice(i, i + size));
  }
  return chunks;
}

// * Deterministic PRNG (mulberry32), seeded like experiment-Lab/experiment.py's
// * rand_seed. Session CSVs store trials blocked by pattern (e.g. 20 identical
// * AAABAAAB trials in a row) - the lab shuffles them before blocking into
// * rest-break groups so pattern type doesn't line up with trial/block position.
// * This isn't bit-identical to pandas' RNG, but serves the same purpose: a
// * fixed, reproducible interleaving instead of raw (blocked) file order.
function mulberry32(seed) {
  return function () {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function seededShuffle(array, seed) {
  const rand = mulberry32(seed);
  const result = array.slice();
  for (let i = result.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [result[i], result[j]] = [result[j], result[i]];
  }
  return result;
}

function randInt(min, max) {
  // * min and max included
  return Math.floor(Math.random() * (max - min + 1) + min);
}

function formatString(template, ...values) {
  return template.replace(/{}/g, () => values.shift());
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function preloadImages(imagePaths) {
  const images = {};
  
  for (let path of imagePaths) {
    const img = new Image();
    img.src = path;
    // * Extract the image name without the extension as the key
    // ! Will break if image name has multiple dots TODO: fix this
    const imageName = path.split("/").pop().split(".")[0];
    // * Use the imageName as the key and the Image object as the value
    images[imageName] = img;
  }
  return images;
}

function initializeImageRows() {

  // const topRowImages = new Array(7).fill(blankImage).concat([questionMarkImg]);
  // const topRowImages = new Array(8).fill(blankImage);
  // const bottomRowImages = new Array(4).fill(blankImage);
  const topRowImages = new Array(8).fill("");
  const bottomRowImages = new Array(4).fill("");

  // * Function to add images to a row
  const addImagesToRow = (rowId, imageSources) => {
    const row = document.getElementById(rowId);
    imageSources.forEach((src) => {
      const img = document.createElement("img");
      img.src = src;
      img.alt = ""; // * Set an appropriate alt text for each image
      row.appendChild(img);
    });
  };

  addImagesToRow("top-row", topRowImages);
  addImagesToRow("bottom-row", bottomRowImages);
}

function setAllImages(state = "show") {
  const topRow = document.querySelectorAll("#" + topRowId + " img");
  const bottomRow = document.querySelectorAll("#" + bottomRowId + " img");
  const allImages = [...topRow, ...bottomRow];

  if (state === "show") {
    allImages.forEach((img) => (img.style.opacity = "1"));
  } else if (state === "hide") {
    allImages.forEach((img) => (img.style.opacity = "0"));
  } else {
    console.error(
      "Invalid state argument for setAllImages(). Use 'show' or 'hide'"
    );
  }
}

function displayTrialImages(rowID, imageSources, maskedImageIdx = null) {
  let currentImages = document.querySelectorAll("#" + rowID + " img");

  // * Set every image's source at once; the masked position shows the question mark.
  // * No reveal order: setAllImages("show") (called by the caller) reveals the whole
  // * row simultaneously, matching the lab experiment's "all stimuli at once" display.
  imageSources.forEach((imageName, idx) => {
    if (idx === maskedImageIdx) {
      currentImages[idx].src = questionMarkImg;
    } else {
      currentImages[idx].src = imageName;
    }
  });
}

async function startImageSequence(
  imageSequences,
  trial_type = "main",
  interTrialInterval = [1000, 3000],
  blockN = -1
) {
  trial_type = ["practice", "main"].includes(trial_type) ? trial_type : "main";


  for (let i = 0; i < imageSequences.length; i++) {
    // * Generate a random wait period between 1 and 3 seconds
    const interTrialTime = randInt(
      interTrialInterval[0],
      interTrialInterval[1]
    );

    log(3, "WaitTime:", interTrialTime);

    // * Wait for the random period before starting the sequence
    // await new Promise((resolve) => setTimeout(resolve, interTrialTime));
    await delay(interTrialTime);

    // Fixed extra 1s pause after the random ITI, matching
    // experiment-Lab/experiment.py's core.wait(1) after the intertrial
    // wait and before stimuli are drawn (experiment.py lines 1009/618).
    await delay(1000);

    let topRowImages = imageSequences[i].topImages.map(
      (imageName) => images[imageName].src
    );

    let bottomRowImages = imageSequences[i].bottomImages.map(
      (imageName) => images[imageName].src
    );
    // let solution = topRowImages.pop().split("/").pop();
    let solution = imageSequences[i].solution;
    let maskedImageIdx = imageSequences[i].maskedImageIdx;

    // * Set all image sources, then reveal the whole sequence + choices at once
    // * (no reveal order), matching the lab experiment's display.
    displayTrialImages(topRowId, topRowImages, maskedImageIdx);
    displayTrialImages(bottomRowId, bottomRowImages);

    midRowText.textContent = "";
    setAllImages("show");
    
    const startTime = Date.now(); // * time right before waiting for a keypress

    const {
      key: KeyPressed,
      index: KeyIndex,
      respTime,
    } = await getResponse(
      startTime,
      config.validKeys,
      config.feedbackTime,
      config.maxRespTime
    );

    let selectedImage;
    let correct;
    // * Check if KeyIndex is not -1 to determine if the key is valid
    if (KeyIndex !== -1) {
      selectedImage = bottomRowImages[KeyIndex].split("/")
        .pop()
        .replace(".png", "");
      correct = solution === selectedImage;
    } else {
      selectedImage = "invalid";
      correct = "invalid";
    }

    if (trial_type === "practice") {
      const bottomImagesElements = document.querySelectorAll(
        "#" + bottomRowId + " img"
      );

      const correctIndex = bottomRowImages.findIndex(
        (img) => img.split("/").pop().replace(".png", "") === solution
      );
      
      bottomImagesElements[correctIndex].classList.add("correct-selection");

      // * Apply feedback based on the correctness
      if (correct === false) {
        bottomImagesElements[KeyIndex].classList.add("incorrect-selection");
      }

      await delay(config.feedbackTime);

      bottomImagesElements.forEach((el) => {
        el.classList.remove("correct-selection", "incorrect-selection");
      });
    }

    let trial_data = {
      blockN: blockN,
      sequenceN: i,
      sequenceID: imageSequences[i].ID,
      pattern: imageSequences[i].pattern,
      keyPressed: KeyPressed,
      keyIndex: KeyIndex,
      selectedImage: selectedImage,
      respTime: respTime,
      correct: correct,
      solution: solution,
    };

    log(3, "Trial Data:", trial_data); // ! TEMP: testing

    responses[trial_type].push(trial_data);

    sendDataToServer(trial_data); // ! TEMP: testing

    setAllImages("hide");
    midRowText.textContent = "+";
  }
}

function getResponse(
  startTime,
  validKeys = ["a", "x", "m", "l"],
  feedbackTime = 2000,
  maxRespTime = null
) {
  return new Promise((resolve) => {
    let timeoutHandler; // * To store the timeout that waits for config.maxRespTime

    const keyHandler = (event) => {
      clearTimeout(timeoutHandler); // * Clear the timeout because a key was pressed
      const key = event.key.toLowerCase();
      const endTime = Date.now(); // * Capture the end time at the moment of key press
      const respTime = endTime - startTime; // * Calculate the response time

      if (config.validKeys.includes(key)) {
        document.removeEventListener("keydown", keyHandler); // * Remove the event listener for valid keys
        const index = config.validKeys.indexOf(key);
        const resp_data = { key: key, index: index, respTime: respTime };
        resolve(resp_data);
      } else {
        // * Handle invalid key press
        const resp_data = {
          key: formatString("invalid:[{}]", key),
          index: -1,
          respTime: respTime,
        };

        setAllImages("hide");

        let midRowText = document.getElementById("mid-row-text");

        midRowText.textContent =
          "Invalid Key Pressed! Please press a valid key (a, x, m, l)";
        // await delay(config.feedbackTime);
        // midRowText.textContent = "+"; // * Clear the message
        // document.removeEventListener("keydown", keyHandler); // * Remove the event listener after timeout
        // resolve(resp_data);
        setTimeout(() => {
          midRowText.textContent = "+"; // * Clear the message
          document.removeEventListener("keydown", keyHandler); // * Remove the event listener after timeout
          resolve(resp_data); // * Also include respTime for invalid keys
        }, config.feedbackTime);
      }
    };

    document.addEventListener("keydown", keyHandler);

    // * Set a timeout to enforce max response time
    timeoutHandler = setTimeout(() => {
      document.removeEventListener("keydown", keyHandler); // * Remove the event listener since time is up
      const resp_data = { key: "invalid", index: -1, respTime: -1 };
      resolve(resp_data); // * Resolve with a timeout indication
    }, config.maxRespTime);
  });
}

async function sendDataToServer(data) {
  try {
    const response = await fetch(config.serverEndpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(data),
    });
    const jsonResponse = await response.json();
    log(2, "Server response:", jsonResponse);
  } catch (error) {
    console.error("Error sending data to server:", error);
  }
}

function waitForValidKey() {
  return new Promise((resolve) => {
    const keyHandler = (event) => {
      const key = event.key.toLowerCase();
      if (config.validKeys.includes(key)) {
        document.removeEventListener("keydown", keyHandler);
        resolve();
      }
    };
    document.addEventListener("keydown", keyHandler);
  });
}

function waitForSpace() {
  return new Promise((resolve) => {
    const keyHandler = (event) => {
      if (event.code === "Space") {
        document.removeEventListener("keydown", keyHandler);
        resolve();
      }
    };
    document.addEventListener("keydown", keyHandler);
  });
}

async function main() {
  initializeImageRows();

  midRowText.textContent = "Loading experiment data...";
  const [practiceRows, sessionRows] = await Promise.all([
    loadCSV(practiceDataPath),
    loadCSV(sessionDataPath),
  ]);
  // The experiment only uses 1 practice trial per pattern (3 total), It
  // picks item_id 1, 2, 4 (get_practice_sequences(), just like in
  // experiment-Lab/experiment.py), so we match that here.
  const practiceItemIds = [1, 2, 4];
  const practiceSequences = processSequences(
    practiceRows
      .filter((row) => practiceItemIds.includes(parseInt(row.item_id)))
      .map((row) => csvRowToSequence(row, nameToId)),
    imageMappings
  );
  const mainSequences = processSequences(
    sessionRows.map((row) => csvRowToSequence(row, nameToId)),
    imageMappings
  );
  // Shuffle before chunking so each block mixes pattern types instead of
  // being one pattern repeated 20x, matching experiment-Lab/experiment.py's
  // sequences.sample(frac=1, random_state=rand_seed) call.
  const rand_seed = 0;
  const shuffledMainSequences = seededShuffle(mainSequences, rand_seed);
  // 220 trials / blockSize 20 = 11 blocks -> 10 rest breaks between them,
  // matching experiment-Lab/experiment.py.
  const blocks = chunk(shuffledMainSequences, config.blockSize);

  midRowText.textContent =
    "Welcome to the experiment! Press the spacebar to continue.";
  await waitForSpace();

  // * PRACTICE TRIALS
  let instr1 =
    "You are going to solve __ abstract reasoning problems like the one below. " +
    "Your goal is to continue the sequence in the top row with one of the four " +
    "options in the bottom row. \nUse the keys a, x, m, l to select one of these " +
    "options from left to right. \nYou will perform three practice trials with " +
    "feedback before the start of the experiment. \n" +
    "Place your fingers on the " +
    formatString("{}, {}, {}, {} ", ...config.validKeys) +
    "keys and press any of them to start the practice trials.";

  midRowText.textContent = instr1;
  await waitForValidKey();
  midRowText.textContent = "+";

  await startImageSequence(practiceSequences, "practice");

  // * MAIN TRIALS
  let instr2 =
    "End of the practice trials. \n" +
    "You are now going to solve __ sequences. You won't receive feedback on your answers anymore.\n" +
    "Be sure to think carefully " +
    "about your response as we plan to compare humans to AI. \n" +
    "Then choose the correct option as quickly as you can.\n" +
    "Place your fingers on the " +
    formatString("{}, {}, {}, {} ", ...config.validKeys) +
    "keys and press any of them to start the experiment.";

  midRowText.textContent = instr2;
  await waitForValidKey();
  midRowText.textContent = "+";

  const startExpTime = Date.now();

  for (let blockN = 0; blockN < blocks.length; blockN++) {
    await startImageSequence(blocks[blockN], "main", [1000, 3000], blockN);

    // * END OF BLOCK -> REST BREAK (skip after the last block)
    if (blockN < blocks.length - 1) {
      midRowText.textContent =
        "Well done! You may take a short break now.\n" +
        "When you're ready to continue, place your fingers on the " +
        formatString("{}, {}, {}, {} ", ...config.validKeys) +
        "keys and press any of them to begin the next block.";
      await waitForValidKey();
      midRowText.textContent = "+";
    }
  }

  const endExpTime = Date.now();
  const expDuration = endExpTime - startExpTime;

  let debrief = "End of Experiment. Thank you for participating!";
  midRowText.textContent = debrief;

  log(1, responses);
  log(0, "Duration:", expDuration / 1000, "s");
}

// * ############### LAUNCH EXPERIMENT ###############
main();
