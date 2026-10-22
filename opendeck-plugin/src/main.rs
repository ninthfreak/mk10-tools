//! Unofficial OpenDeck device plugin for the Waveshare MK10 (firmware V2.32 or newer).
//!
//! Run by OpenDeck normally. Run by hand with `--test [PORT]` to check the device without OpenDeck.

mod device;
mod font;
mod plugin;
mod protocol;
mod screen;
mod selftest;

fn main() {
    let _ = simplelog::TermLogger::init(
        simplelog::LevelFilter::Info,
        simplelog::Config::default(),
        simplelog::TerminalMode::Stdout,
        simplelog::ColorChoice::Never,
    );
    let args: Vec<String> = std::env::args().collect();

    if args.iter().any(|a| a == "--version") {
        println!("opendeck-mk10 {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    if let Some(i) = args.iter().position(|a| a == "--test") {
        if let Err(e) = selftest::run(args.get(i + 1).cloned()) {
            eprintln!("Test failed: {e}");
            std::process::exit(1);
        }
        return;
    }
    if !args.iter().any(|a| a.eq_ignore_ascii_case("-port")) {
        println!("opendeck-mk10 {}: an OpenDeck device plugin for the Waveshare MK10.", env!("CARGO_PKG_VERSION"));
        println!("Install it from OpenDeck (Plugins > Install from file). To check a device by hand: --test [PORT]");
        return;
    }

    let rt = tokio::runtime::Builder::new_multi_thread().enable_all().build().expect("tokio runtime");
    rt.block_on(async {
        if let Err(e) = openaction::init_plugin(plugin::Global, plugin::Actions).await {
            log::error!("plugin stopped: {e}");
            std::process::exit(1);
        }
    });
}
