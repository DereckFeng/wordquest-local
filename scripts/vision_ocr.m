#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>
#import <Vision/Vision.h>

static NSDictionary *Recognize(NSURL *url, CGImagePropertyOrientation orientation, NSError **error) {
    VNRecognizeTextRequest *request = [[VNRecognizeTextRequest alloc] init];
    request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
    request.recognitionLanguages = @[@"en-US"];
    request.usesLanguageCorrection = YES;
    request.minimumTextHeight = 0.005;

    VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithURL:url orientation:orientation options:@{}];
    if (![handler performRequests:@[request] error:error]) {
        return nil;
    }

    NSMutableArray *lines = [NSMutableArray array];
    double score = 0.0;
    for (VNRecognizedTextObservation *observation in request.results) {
        VNRecognizedText *candidate = [[observation topCandidates:1] firstObject];
        if (candidate == nil) continue;
        CGRect box = observation.boundingBox;
        score += candidate.confidence * MAX(1, candidate.string.length);
        [lines addObject:@{
            @"text": candidate.string,
            @"confidence": @(candidate.confidence),
            @"x": @(box.origin.x),
            @"y": @(box.origin.y),
            @"width": @(box.size.width),
            @"height": @(box.size.height),
        }];
    }
    [lines sortUsingComparator:^NSComparisonResult(NSDictionary *left, NSDictionary *right) {
        double leftY = [left[@"y"] doubleValue];
        double rightY = [right[@"y"] doubleValue];
        if (fabs(leftY - rightY) > 0.01) return leftY > rightY ? NSOrderedAscending : NSOrderedDescending;
        return [left[@"x"] doubleValue] < [right[@"x"] doubleValue] ? NSOrderedAscending : NSOrderedDescending;
    }];
    return @{@"score": @(score), @"lines": lines};
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        for (int index = 1; index < argc; index++) {
            NSString *path = [NSString stringWithUTF8String:argv[index]];
            NSURL *url = [NSURL fileURLWithPath:path];
            NSError *upError = nil;
            NSDictionary *up = Recognize(url, kCGImagePropertyOrientationUp, &upError);

            NSMutableDictionary *output = [NSMutableDictionary dictionaryWithObject:path forKey:@"path"];
            if (up == nil) {
                output[@"error"] = upError.localizedDescription ?: @"unknown Vision error";
            } else {
                output[@"orientation"] = @"up";
                output[@"score"] = up[@"score"];
                output[@"lines"] = up[@"lines"];
            }
            NSData *json = [NSJSONSerialization dataWithJSONObject:output options:0 error:nil];
            fwrite(json.bytes, 1, json.length, stdout);
            fputc('\n', stdout);
            fflush(stdout);
        }
    }
    return 0;
}
