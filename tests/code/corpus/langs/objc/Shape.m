#import <Foundation/Foundation.h>

@interface Shape : NSObject
- (double)area;
- (NSString *)describe;
@end

@implementation Shape
- (double)area {
    return 0.0;
}

- (NSString *)describe {
    return [NSString stringWithFormat:@"%f", [self area]];
}
@end

@interface Circle : Shape
- (double)area;
@end

@implementation Circle
- (double)area {
    return 3.14159;
}
@end
